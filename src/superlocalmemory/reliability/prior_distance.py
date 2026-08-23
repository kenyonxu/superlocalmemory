# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com | https://varunpratap.com

"""Has a Bayesian learner's posterior actually moved off its prior?

A Thompson-sampling selector over Beta posteriors records a play, applies a
reward, and reports both. None of that tells you whether it learned anything.
Its own counters cannot: a reward channel that emits one constant value
increments the posterior on every play, so the play count rises, the timestamps
advance, and the dashboard looks alive while the distribution never moves.

This is not a hypothetical. A Beta posterior updated as
``alpha += r; beta += (1 - r)`` is stationary in mean for exactly one reward
value: ``r = 0.5``. And that value is the usual neutral fallback when a reward
cannot be attributed to a play. So the failure that produces *no learning at
all* is also the failure that produces *the most normal-looking counters*.

The signature is exact, not statistical. With a ``Beta(a0, b0)`` prior and *n*
observations all equal to 0.5::

    alpha - a0  ==  beta - b0  ==  n * 0.5

0.5 is exactly representable in binary floating point, so ``n * 0.5`` is exact
for any plausible *n*. A learner matching that identity on every unit has
provably received the neutral value every single time — there is no sampling
noise to argue about, and one run is enough to establish it.

What this check does NOT claim: that a moving posterior is a *good* one. Motion
off the prior is necessary for learning, not sufficient. This distinguishes
"receiving signal" from "receiving nothing", which is the distinction the
learner's own metrics cannot make.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("superlocalmemory.reliability.prior_distance")

#: Below this many observations in total, a posterior sitting at its prior is
#: expected rather than suspicious, so no verdict is issued.
DEFAULT_MIN_OBSERVATIONS = 20

#: And below this many observations *per unit*. An aggregate floor alone lets a
#: store with many units and few observations report STALLED on units that were
#: never played at all.
_MIN_OBSERVATIONS_PER_UNIT = 2.0

#: Distance from the prior mean below which a unit counts as unmoved. Kept well
#: above float noise so that a genuinely tiny update is not reported as none.
_MEAN_EPSILON = 1e-9

#: Beta learners in this store, as (table, unit column, observation column).
#: Each is a Beta(1, 1) posterior over a named unit.
_BETA_LEARNERS: tuple[tuple[str, str, str | None], ...] = (
    ("bandit_arms", "arm_id", "plays"),
    ("source_quality", "source_id", None),
)

_PRIOR_ALPHA = 1.0
_PRIOR_BETA = 1.0


@dataclass(frozen=True)
class LearnerVerdict:
    """One Beta learner, and whether its posterior has moved."""

    table: str
    units: int
    units_at_prior_mean: int
    units_matching_neutral_identity: int
    observations: int
    verdict: str
    detail: str
    sample: tuple[tuple[str, float, float], ...] = field(default=())

    @property
    def is_stalled(self) -> bool:
        return self.verdict == "STALLED"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _inspect_learner(
    conn: sqlite3.Connection,
    table: str,
    unit_column: str,
    observation_column: str | None,
    *,
    min_observations: int,
) -> LearnerVerdict | None:
    """Inspect one Beta learner. Returns None when the table is absent."""
    if not _table_exists(conn, table):
        return None

    available = _columns(conn, table)
    if not {"alpha", "beta"}.issubset(available):
        return None
    unit = unit_column if unit_column in available else "rowid"
    obs_col = observation_column if (observation_column or "") in available else None

    select = f'SELECT "{unit}", alpha, beta'
    select += f', "{obs_col}"' if obs_col else ", NULL"
    rows = conn.execute(f'{select} FROM "{table}"').fetchall()
    if not rows:
        return None

    units = len(rows)
    at_prior_mean = 0
    neutral_identity = 0
    observations = 0
    for _unit, alpha, beta, obs in rows:
        alpha = float(alpha or 0.0)
        beta = float(beta or 0.0)
        total = alpha + beta
        if total > 0 and abs(alpha / total - 0.5) < _MEAN_EPSILON:
            at_prior_mean += 1
        n = int(obs) if obs is not None else None
        if n is None:
            # No per-unit observation count. Infer it from the identity itself:
            # n = (alpha - a0) / 0.5 only holds if every reward was neutral, so
            # this is checked, never assumed.
            candidate = (alpha - _PRIOR_ALPHA) * 2.0
            n = int(round(candidate)) if candidate >= 0 else 0
        observations += n
        if n > 0 and (
            abs((alpha - _PRIOR_ALPHA) - n * 0.5) < _MEAN_EPSILON
            and abs((beta - _PRIOR_BETA) - n * 0.5) < _MEAN_EPSILON
        ):
            neutral_identity += 1

    sample = tuple(
        (str(r[0]), float(r[1] or 0.0), float(r[2] or 0.0)) for r in rows[:3]
    )

    # The floor has to bind per unit, not in aggregate. 165 arms sharing 20
    # observations leaves most of them untouched at exactly the prior, which
    # satisfies the unmoved test for a reason that carries no information. A
    # verdict of STALLED must mean "measured inert", never "too sparse to tell".
    per_unit = observations / units if units else 0.0
    if observations < min_observations or per_unit < _MIN_OBSERVATIONS_PER_UNIT:
        verdict = "INSUFFICIENT_DATA"
        detail = (
            f"{observations} observations across {units} units "
            f"({per_unit:.2f} per unit) is too sparse for an unmoved posterior to "
            f"mean anything; the floors are {min_observations} in total and "
            f"{_MIN_OBSERVATIONS_PER_UNIT:g} per unit."
        )
    elif neutral_identity == units:
        verdict = "STALLED"
        detail = (
            f"All {units} units satisfy (alpha-{_PRIOR_ALPHA:g}) == "
            f"(beta-{_PRIOR_BETA:g}) == n/2 exactly across {observations} "
            f"observations, so the rewards sum to exactly n/2 on every unit and "
            f"each posterior mean is still {0.5}. No unit has acquired any "
            f"preference. Note the identity constrains the SUM: it is also "
            f"satisfied by a symmetric non-neutral stream, so confirm against a "
            f"per-observation record before concluding the reward was constant."
        )
    elif at_prior_mean == units:
        verdict = "STALLED"
        detail = (
            f"All {units} units sit at posterior mean 0.5 after {observations} "
            f"observations, without matching the exact neutral identity. The "
            f"updates are symmetric but not uniformly 0.5 — inspect the reward "
            f"source."
        )
    else:
        moved = units - at_prior_mean
        verdict = "MOVING"
        detail = (
            f"{moved} of {units} units have moved off the prior mean across "
            f"{observations} observations."
        )

    return LearnerVerdict(
        table=table,
        units=units,
        units_at_prior_mean=at_prior_mean,
        units_matching_neutral_identity=neutral_identity,
        observations=observations,
        verdict=verdict,
        detail=detail,
        sample=sample,
    )


def check_beta_learners(
    learning_db: Any,
    *,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> list[LearnerVerdict]:
    """Report, per Beta learner in ``learning_db``, whether it has learned.

    ``learning_db`` may be a path or an open :class:`sqlite3.Connection`. The
    database is only read. Fail-soft: any error yields an empty list and a log
    line, because a diagnostic must never be the reason something breaks.
    """
    owns_connection = not isinstance(learning_db, sqlite3.Connection)
    conn: sqlite3.Connection | None = None
    try:
        if owns_connection:
            conn = sqlite3.connect(f"file:{learning_db}?mode=ro", uri=True)
        else:
            conn = learning_db
        out: list[LearnerVerdict] = []
        for table, unit_column, observation_column in _BETA_LEARNERS:
            verdict = _inspect_learner(
                conn,
                table,
                unit_column,
                observation_column,
                min_observations=min_observations,
            )
            if verdict is not None:
                out.append(verdict)
        return out
    except Exception:
        logger.debug("prior-distance check skipped", exc_info=True)
        return []
    finally:
        if owns_connection and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


__all__ = ["DEFAULT_MIN_OBSERVATIONS", "LearnerVerdict", "check_beta_learners"]
