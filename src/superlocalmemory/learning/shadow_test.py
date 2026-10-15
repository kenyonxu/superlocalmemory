# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory — LLD-10 shadow validation, rebuilt in 4.1.25

"""Live A/B validation of a retrained ranker: promote, reject, or keep going.

WHAT WAS WRONG
-----------------------------
Each recall is routed to ONE arm — the active model or the candidate. The old
validator enforced exactly that (a query id may live on one arm only) and then
ran a *paired* t-test over the intersection of the two arms' query ids. The
intersection of two disjoint sets is empty, so ``decide()`` returned
``continue`` forever: a retrained ranker could never be promoted or rejected,
however much evidence accumulated. Pairing by arrival index instead would have
produced a number, but not a paired comparison: the k-th active query and the
k-th candidate query are different questions.

THE DESIGN NOW
--------------
Disjoint randomized traffic is an **independent-groups** experiment. Each
settled recall contributes one reward in ``[0, 1]`` to the arm that served it;
the quantity under test is the difference of arm means,
``D = mean(candidate) - mean(active)``.

Because the decision is re-evaluated after every observation (continuous
monitoring), a fixed-sample test would inflate the false-promotion rate. We use
a **mixture sequential probability ratio test** (mSPRT), the always-valid
procedure described by Johari, Pekelis and Walsh, *Always Valid Inference:
Bringing Sequential Analysis to A/B Testing* (arXiv:1512.04922; Section 5.2
eq. (7)-(8) and the two-stream construction of Section 6.1 / Appendix B eq.
(22)). The test rejects the null "no difference" the first time the mixture
likelihood ratio reaches ``1/alpha``; Type I error is controlled at ``alpha``
uniformly over the stopping time.

For normal data their two-stream statistic depends on the data only through
the difference of means, distributed ``N(theta, 2 sigma^2 / n)`` with ``n``
observations per arm (Appendix B). With a normal mixing distribution
``H = N(0, gamma^2)`` the integral in (22) has a closed form. Writing ``V``
for the variance of ``D``:

    Lambda = sqrt(V / (V + gamma^2)) * exp(gamma^2 D^2 / (2 V (V + gamma^2)))

With equal arm sizes ``V = 2 sigma^2 / n`` and this is exactly (22). The
closed form is our own evaluation of that integral, not quoted from the paper.
SHA-256 routing gives arms of unequal size, so we use
``V = s^2 (1/n_active + 1/n_candidate)``, the variance of a difference of two
independent group means. That generalisation is ours too; the paper notes the
approach extends to uneven allocation fixed in advance (Section 6.1), which a
50/50 hash split is.

Honest limits, stated rather than hidden:

* Rewards are bounded (0, 0.5, 1 for explicit outcomes; engagement in
  ``[0, 1]``), not normal, and ``s^2`` is a pooled plug-in estimate. The paper
  says the analogous plug-in construction for binary data is only
  *approximately* always valid, "for alpha small" (Section 6.1). We therefore
  enforce ``MIN_PER_ARM`` observations before any decision and a variance
  floor, and ``tests/test_learning/test_shadow_test.py`` measures the
  false-promotion rate of the exact production code under A/A continuous
  monitoring (it must stay below ``ALPHA``).
* ``gamma`` (``MIX_SD``) is a design choice: 0.1 on a ``[0, 1]`` reward, the
  scale of effect we care to detect. The paper shows the run-time-optimal
  mixture depends on the prior over effects (Prop. 6) and that performance is
  robust to misspecification (Section 5).

Decision rule (``ALPHA`` = 0.05, two-sided):

* ``promote`` — ``Lambda >= 1/ALPHA`` and ``D >= MIN_EFFECT``.
* ``reject``  — ``Lambda >= 1/ALPHA`` and ``D < 0`` (significantly worse), or
  both arms reached ``MAX_PER_ARM`` without a promotion (truncation; stopping
  early can only lower Type I error).
* ``continue`` otherwise.

Replays are idempotent: the first observation for a query id wins, whichever
arm it arrives on, in memory and in ``shadow_observations`` (whose
``ndcg_at_10`` column name is historical — it stores the settled reward; the value was never an NDCG).
"""

from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
from datetime import datetime, timezone
from typing import Final

logger = logging.getLogger(__name__)

__all__ = ("ShadowTest", "msprt_lambda")

#: Two-sided Type I error budget for the always-valid test.
_ALPHA: Final[float] = 0.05
#: Standard deviation of the normal mixing distribution over effects.
_MIX_SD: Final[float] = 0.1
#: Smallest improvement worth promoting, on the reward scale.
_MIN_EFFECT: Final[float] = 0.02
#: Observations per arm before any decision (plug-in variance stability).
_MIN_PER_ARM: Final[int] = 30
#: Truncation horizon per arm (kept from LLD-10's sample-size contract).
_MAX_PER_ARM: Final[int] = 885
#: Floor on the pooled variance, so a lucky streak of identical rewards
#: cannot make the evidence look infinite.
_VAR_FLOOR: Final[float] = 0.0025

_ARMS: Final[tuple[str, str]] = ("active", "candidate")


def msprt_lambda(diff: float, var_diff: float, mix_sd: float = _MIX_SD) -> float:
    """Normal-mixture likelihood ratio for an observed difference of means.

    Computed in log space; returns ``inf`` rather than overflowing.
    """
    v = max(float(var_diff), 1e-12)
    g2 = float(mix_sd) ** 2
    log_l = 0.5 * math.log(v / (v + g2)) + g2 * diff * diff / (2.0 * v * (v + g2))
    return math.inf if log_l > 700.0 else math.exp(log_l)


def _moments(acc: list[float]) -> tuple[int, float, float]:
    """``(n, mean, sample variance)`` from running ``[n, sum, sum_sq]``."""
    n = int(acc[0])
    if n == 0:
        return 0, 0.0, 0.0
    mean = acc[1] / n
    if n < 2:
        return n, mean, 0.0
    return n, mean, max(0.0, (acc[2] - n * mean * mean) / (n - 1))


class ShadowTest:
    """Independent-groups, always-valid A/B validator for one candidate.

    Callers record one settled reward per query id with
    :meth:`record_observation` (the arm comes from the persisted serve-time
    assignment, never from a recomputation), then call :meth:`decide`.
    """

    ALPHA: Final[float] = _ALPHA
    MIX_SD: Final[float] = _MIX_SD
    MIN_EFFECT: Final[float] = _MIN_EFFECT
    MIN_PER_ARM: Final[int] = _MIN_PER_ARM
    MAX_PER_ARM: Final[int] = _MAX_PER_ARM
    #: Kept for callers that read the old names: the truncation horizon.
    PHASE_B_N: Final[int] = _MAX_PER_ARM

    def __init__(
        self,
        profile_id: str,
        candidate_model_id: str,
        *,
        learning_db: str | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.candidate_model_id = candidate_model_id
        self._learning_db = learning_db
        # query_id -> (arm, reward). One entry per query, first write wins.
        self._obs: dict[str, tuple[str, float]] = {}
        # Running [n, sum, sum of squares] per arm, so decide() is O(1).
        self._acc: dict[str, list[float]] = {a: [0.0, 0.0, 0.0] for a in _ARMS}
        if learning_db:
            self._reload_from_db()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _candidate_int(self) -> int | None:
        try:
            return int(self.candidate_model_id)
        except (TypeError, ValueError):
            return None

    def _reload_from_db(self) -> None:
        """Restore observations after a restart. Fail-soft on a cold store."""
        cid = self._candidate_int()
        if cid is None:
            return
        try:
            conn = sqlite3.connect(self._learning_db, timeout=2.0)  # type: ignore[arg-type]
        except sqlite3.Error:  # pragma: no cover — defensive
            return
        try:
            rows = conn.execute(
                "SELECT arm, query_id, ndcg_at_10 FROM shadow_observations "
                "WHERE candidate_id = ? ORDER BY id ASC",
                (cid,),
            ).fetchall()
        except sqlite3.Error:
            rows = []  # M012 not applied yet
        finally:
            conn.close()
        for arm, qid, reward in rows:
            if arm in _ARMS and str(qid) not in self._obs:
                self._add(str(qid), arm, float(reward))

    def _persist(self, query_id: str, arm: str, reward: float) -> None:
        cid = self._candidate_int()
        if not self._learning_db or cid is None:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            conn = sqlite3.connect(self._learning_db, timeout=2.0)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_observations "
                    "(profile_id, candidate_id, query_id, arm, ndcg_at_10, "
                    " recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (self.profile_id, cid, query_id, arm, reward, now),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.debug("shadow observation not persisted: %s", exc)

    # ------------------------------------------------------------------
    # Routing (kept for callers without an install token)
    # ------------------------------------------------------------------

    def route_query(self, query_id: str) -> str:
        """Deterministic 50/50 route: SHA-256 first 8 hex chars, modulo 2."""
        h = hashlib.sha256(query_id.encode("utf-8")).hexdigest()[:8]
        return "candidate" if int(h, 16) % 2 == 1 else "active"

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def record_observation(self, *, query_id: str, arm: str, reward: float) -> bool:
        """Record one settled reward. Returns False for a replay or bad arm."""
        qid = str(query_id)
        if arm not in _ARMS or qid in self._obs:
            return False
        value = max(0.0, min(1.0, float(reward)))
        self._add(qid, arm, value)
        self._persist(qid, arm, value)
        return True

    def _add(self, query_id: str, arm: str, value: float) -> None:
        self._obs[query_id] = (arm, value)
        acc = self._acc[arm]
        acc[0] += 1.0
        acc[1] += value
        acc[2] += value * value

    def record_recall_pair(
        self, *, query_id: str, arm: str,
        ndcg_at_10: float | None = None, reward: float | None = None,
    ) -> bool:
        """Back-compatible alias. ``ndcg_at_10`` was always a reward."""
        value = reward if reward is not None else ndcg_at_10
        if value is None:
            return False
        return self.record_observation(query_id=query_id, arm=arm, reward=value)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def decide(self) -> tuple[str, dict]:
        """Return ``(decision, stats)`` — ``promote``, ``reject`` or ``continue``."""
        n_a, mean_a, var_a = _moments(self._acc["active"])
        n_c, mean_c, var_c = _moments(self._acc["candidate"])
        stats: dict = {
            "design": "independent_groups_msprt", "alpha": self.ALPHA,
            "n_active": n_a, "n_candidate": n_c,
            "mean_active": mean_a, "mean_candidate": mean_c,
            "effect": mean_c - mean_a, "lambda": 0.0, "criterion": None,
        }
        if min(n_a, n_c) < self.MIN_PER_ARM:
            stats["criterion"] = "accumulating"
            return "continue", stats
        pooled = ((n_a - 1) * var_a + (n_c - 1) * var_c) / (n_a + n_c - 2)
        var_diff = max(pooled, _VAR_FLOOR) * (1.0 / n_a + 1.0 / n_c)
        lam = msprt_lambda(stats["effect"], var_diff, self.MIX_SD)
        stats["lambda"] = lam
        stats["var_diff"] = var_diff
        if lam >= 1.0 / self.ALPHA:
            if stats["effect"] >= self.MIN_EFFECT:
                stats["criterion"] = "msprt_candidate_better"
                return "promote", stats
            if stats["effect"] < 0.0:
                stats["criterion"] = "msprt_candidate_worse"
                return "reject", stats
        if min(n_a, n_c) >= self.MAX_PER_ARM:
            stats["criterion"] = "horizon_without_win"
            return "reject", stats
        stats["criterion"] = "inconclusive"
        return "continue", stats
