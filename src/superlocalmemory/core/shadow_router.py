# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory — Stage 8 SB-1 / LLD-10, rebuilt in 4.1.25

"""Recall-time A/B router: serve, record, decide, promote, watch.

The single seam between the live recall path and the two model-lifecycle
state machines: :class:`~superlocalmemory.learning.shadow_test.ShadowTest`
(before promotion) and :class:`~superlocalmemory.learning.model_rollback.ModelRollback`
(after it).

WHAT CHANGED IN 4.1.25
-------------------------------------
* **The candidate is actually served.** ``assign()`` runs at recall time,
  routes the query, and records ``(candidate_id, query_id, arm,
  served_model_id)`` in ``shadow_assignments`` (M055). Before, nothing served
  the candidate at all: both "arms" were ranked by the active model, and the
  arm was recomputed from the query id only when the reward settled.
* **The settled reward goes to the arm that served it,** read back from that
  persisted row, so a restart or a token rotation cannot move it. A reward for
  a query that was not served under the current test is ignored.
* **The decision can be reached.** ShadowTest is an independent-groups
  always-valid test now (see its module docstring), so both ``promote`` and
  ``reject`` happen.
* **Reject is durable.** A rejected candidate is retired in
  ``learning_model_state`` (``is_candidate = 0``) with its statistics, instead
  of being re-attached from the database on the next restart.
* **Promotion arms the rollback watch.** ``arm_post_promotion_watch`` had no
  caller, so the advertised auto-rollback could never fire. The watch baseline
  is the active arm's mean reward from the same experiment — the same metric
  the watch then measures.
* **One router per learning store.** The cache used to key on the memory.db
  path too; the trainer attached candidates with ``memory_db=""`` while the
  settlement path asked with the real path, so they talked to two different
  routers. The router never reads memory.db, so it is no longer part of the key.
* **Candidates trained in another process are seen.** The candidate id is
  refreshed from ``learning_model_state`` at most every ``_REFRESH_SEC``.

Fail-soft: every exception on the recall or settlement path is logged and
swallowed — recall integrity comes first.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from superlocalmemory.core.security_primitives import ensure_install_token
from superlocalmemory.learning.model_rollback import ModelRollback
from superlocalmemory.learning.shadow_test import ShadowTest

logger = logging.getLogger(__name__)

ARM_BASELINE: Final[str] = "baseline"
ARM_CANDIDATE: Final[str] = "candidate"

#: Seconds between candidate-id refreshes from the learning store.
_REFRESH_SEC: Final[float] = 30.0


@dataclass(frozen=True)
class Assignment:
    """Which arm served a recall under a shadow test, and with what model."""

    candidate_id: int
    arm: str
    served_model_id: int | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ShadowRouter:
    """Per-(learning store, profile) A/B router and post-promotion watcher."""

    def __init__(self, *, learning_db: str, profile_id: str,
                 memory_db: str = "") -> None:
        self._learning_db = str(learning_db)
        self._profile_id = str(profile_id)
        self._memory_db = str(memory_db)  # unused; kept for callers
        self._shadow: ShadowTest | None = None
        self._candidate_id: int | None = None
        self._rollback: ModelRollback | None = None
        self._checked_at = 0.0
        self._lock = threading.RLock()
        # Pinned so a token rotation mid-test cannot flip arms (S9-SKEP-11).
        try:
            self._routing_token = ensure_install_token()
        except Exception:  # pragma: no cover — defensive
            self._routing_token = ""
        self.refresh_candidate(force=True)

    # ------------------------------------------------------------------
    # Candidate discovery
    # ------------------------------------------------------------------

    def _read_candidate_id(self) -> int | None:
        try:
            conn = sqlite3.connect(self._learning_db, timeout=2.0)
            try:
                row = conn.execute(
                    "SELECT id FROM learning_model_state "
                    "WHERE profile_id = ? AND is_candidate = 1 LIMIT 1",
                    (self._profile_id,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return int(row[0]) if row and row[0] is not None else None

    def refresh_candidate(self, *, force: bool = False) -> int | None:
        """Re-read the candidate id (TTL-limited). Returns the current id."""
        now = time.monotonic()
        with self._lock:
            if not force and now - self._checked_at < _REFRESH_SEC:
                return self._candidate_id
            self._checked_at = now
            cid = self._read_candidate_id()
            if cid != self._candidate_id:
                self._candidate_id = cid
                self._shadow = (None if cid is None else ShadowTest(
                    profile_id=self._profile_id, candidate_model_id=str(cid),
                    learning_db=self._learning_db,
                ))
            return self._candidate_id

    def attach_candidate(self, candidate_id: int) -> None:
        """Start a fresh test for a just-persisted candidate."""
        with self._lock:
            self._candidate_id = int(candidate_id)
            self._checked_at = time.monotonic()
            self._shadow = ShadowTest(
                profile_id=self._profile_id,
                candidate_model_id=str(candidate_id),
                learning_db=self._learning_db,
            )

    # ------------------------------------------------------------------
    # Routing + serve-time assignment
    # ------------------------------------------------------------------

    def route_query(self, query_id: str) -> str:
        """``'baseline'`` or ``'candidate'``, keyed by install token + query id."""
        digest = hashlib.sha256(
            (self._routing_token + str(query_id)).encode("utf-8"),
        ).hexdigest()[:8]
        return ARM_CANDIDATE if int(digest, 16) % 2 == 1 else ARM_BASELINE

    def assign(self, query_id: str, *,
               active_model_id: int | None) -> Assignment | None:
        """Route and persist one recall while a candidate is under test.

        Returns ``None`` when there is no candidate. A replayed query id gets
        its ORIGINAL assignment back (``INSERT OR IGNORE`` then read).
        """
        cid = self.refresh_candidate()
        if cid is None:
            return None
        arm = self.route_query(query_id)
        served = cid if arm == ARM_CANDIDATE else active_model_id
        try:
            conn = sqlite3.connect(self._learning_db, timeout=2.0)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_assignments "
                    "(candidate_id, query_id, profile_id, arm, "
                    " served_model_id, assigned_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, str(query_id), self._profile_id, arm, served,
                     _now_iso()),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT arm, served_model_id FROM shadow_assignments "
                    "WHERE candidate_id = ? AND query_id = ?",
                    (cid, str(query_id)),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.debug("shadow assignment not recorded: %s", exc)
            return None
        if row is None:  # pragma: no cover — defensive
            return None
        return Assignment(cid, str(row[0]),
                          None if row[1] is None else int(row[1]))

    def lookup(self, query_id: str) -> Assignment | None:
        """The persisted assignment of ``query_id`` under the current test."""
        cid = self.refresh_candidate()
        if cid is None:
            return None
        try:
            conn = sqlite3.connect(self._learning_db, timeout=2.0)
            try:
                row = conn.execute(
                    "SELECT arm, served_model_id FROM shadow_assignments "
                    "WHERE candidate_id = ? AND query_id = ?",
                    (cid, str(query_id)),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return Assignment(cid, str(row[0]),
                          None if row[1] is None else int(row[1]))

    # ------------------------------------------------------------------
    # Settlement ingestion
    # ------------------------------------------------------------------

    def arm_post_promotion_watch(self, *, baseline_reward: float | None = None,
                                 baseline_ndcg: float | None = None) -> None:
        """Start the 200-recall post-promotion watch.

        ``baseline_ndcg`` is the pre-4.1.25 name of the same number; it was
        never an NDCG and is accepted only for old callers.
        """
        base = baseline_reward if baseline_reward is not None else baseline_ndcg
        self._rollback = ModelRollback(
            learning_db_path=self._learning_db, profile_id=self._profile_id,
            baseline_reward=float(base or 0.0),
        )

    def on_recall_settled(
        self, *, query_id: str, reward: float | None = None,
        ndcg_at_10: float | None = None, arm: str | None = None,
    ) -> str | None:
        """Feed one settled reward. Returns the shadow decision, if any.

        ``arm`` is ignored: the persisted assignment is authoritative.
        ``ndcg_at_10`` is the legacy name of ``reward``.
        """
        del arm
        value = reward if reward is not None else ndcg_at_10
        if value is None:
            return None
        try:
            with self._lock:
                decision = self._feed_shadow(str(query_id), float(value))
                self._feed_rollback(str(query_id), float(value))
            return decision
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("shadow_router on_recall_settled error: %s", exc)
            return None

    def _feed_shadow(self, query_id: str, reward: float) -> str | None:
        if self._shadow is None or self._candidate_id is None:
            return None
        assignment = self.lookup(query_id)
        if assignment is None or assignment.candidate_id != self._candidate_id:
            return None  # not served under this test
        st_arm = "candidate" if assignment.arm == ARM_CANDIDATE else "active"
        self._shadow.record_observation(query_id=query_id, arm=st_arm,
                                        reward=reward)
        decision, stats = self._shadow.decide()
        if decision == "promote":
            self._fire_promotion(stats)
        elif decision == "reject":
            self._fire_rejection(stats)
        return decision

    def _feed_rollback(self, query_id: str, reward: float) -> None:
        if self._rollback is None:
            return
        self._rollback.record_post_promotion(query_id=query_id, reward=reward)
        if self._rollback.should_rollback():
            self._fire_rollback()

    # ------------------------------------------------------------------
    # Lineage transitions
    # ------------------------------------------------------------------

    def _fire_promotion(self, stats: dict) -> None:
        cid = self._candidate_id
        if cid is None:
            return
        try:
            from superlocalmemory.learning.consolidation_worker import (
                _promote_candidate,
            )
            if _promote_candidate(self._learning_db, profile_id=self._profile_id,
                                  candidate_id=int(cid)):
                self._record_results(cid, "promoted", stats)
                self.arm_post_promotion_watch(
                    baseline_reward=float(stats.get("mean_active", 0.0)))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("shadow_router promotion failed: %s", exc)
        finally:
            self._shadow = None
            self._candidate_id = None

    def _fire_rejection(self, stats: dict) -> None:
        cid = self._candidate_id
        try:
            if cid is not None:
                conn = sqlite3.connect(self._learning_db, timeout=5.0)
                try:
                    conn.execute(
                        "UPDATE learning_model_state SET is_candidate = 0 "
                        "WHERE id = ? AND is_candidate = 1", (int(cid),))
                    conn.commit()
                finally:
                    conn.close()
                self._record_results(cid, "rejected", stats)
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning("shadow_router rejection not persisted: %s", exc)
        finally:
            self._shadow = None
            self._candidate_id = None

    def _record_results(self, cid: int, verdict: str, stats: dict) -> None:
        payload = {k: v for k, v in stats.items()
                   if isinstance(v, (int, float, str, type(None)))}
        payload["verdict"] = verdict
        payload["decided_at"] = _now_iso()
        try:
            conn = sqlite3.connect(self._learning_db, timeout=5.0)
            try:
                conn.execute(
                    "UPDATE learning_model_state SET shadow_results_json = ? "
                    "WHERE id = ?",
                    (json.dumps(payload, default=str), int(cid)))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.debug("shadow results not recorded: %s", exc)

    def _fire_rollback(self) -> None:
        try:
            if self._rollback is not None:
                self._rollback.execute_rollback(reason="watch_window_regression")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("shadow_router rollback failed: %s", exc)
        finally:
            self._rollback = None


# ---------------------------------------------------------------------------
# Process-local cache: one router per (learning store, profile).
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[str, str], ShadowRouter] = {}
_CACHE_LOCK = threading.Lock()


def get_shadow_router(*, learning_db: str, profile_id: str,
                      memory_db: str = "") -> ShadowRouter:
    """Return the process-local router for ``(learning_db, profile_id)``.

    ``memory_db`` is accepted for old callers and ignored: keying on it split
    one experiment across two routers (see the module docstring).
    """
    key = (str(learning_db), str(profile_id))
    with _CACHE_LOCK:
        router = _CACHE.get(key)
        if router is None:
            router = ShadowRouter(learning_db=learning_db,
                                  profile_id=profile_id, memory_db=memory_db)
            _CACHE[key] = router
        return router


def reset_for_testing() -> None:
    """Clear the singleton cache — tests only."""
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = (
    "ARM_BASELINE",
    "ARM_CANDIDATE",
    "Assignment",
    "ShadowRouter",
    "get_shadow_router",
    "reset_for_testing",
)
