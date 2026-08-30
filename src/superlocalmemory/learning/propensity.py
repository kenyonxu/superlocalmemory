# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Inverse-propensity weighting, so the bandit cannot confirm itself.

THE BIAS
--------
The bandit decides what is shown, and engagement is then measured on what was
shown. Feed that back raw and the loop is circular: an arm ranked first is seen
more, so it is engaged with more, so it is ranked first more. The arm that wins
is the one that was already winning, and the posterior records popularity it
manufactured rather than usefulness it discovered.

That is the self-referential signal in its exact form — a measurement taken
through the mechanism it is meant to evaluate cannot see that mechanism fail.

THE CORRECTION
--------------
Weight each observation by the inverse of the probability the policy had of
showing that arm. An arm the policy was unlikely to show, that was engaged with
anyway, is strong evidence; an arm the policy shows almost always is weak
evidence whatever happens to it. This is the standard IPS estimator from
counterfactual learning-to-rank, and it makes the update unbiased with respect
to the policy's own choices.

Under Thompson sampling the propensity is not a stored number: an arm is shown
when its posterior draw beats every competitor's, so the probability is
``P(theta_i > theta_j for all j != i)`` with each ``theta ~ Beta(alpha, beta)``.
There is no closed form for more than two arms, so it is estimated by sampling.

WHEN THE COMPETITORS ARE UNKNOWN
--------------------------------
Return a weight of exactly 1.0 — no correction. A wrong correction is worse
than none: it would silently scale evidence by a number with no meaning, and
unlike an absent correction nothing downstream could tell. Abstaining from a
correction is visible in ``PropensityEstimate.corrected``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = ["PropensityEstimate", "estimate_propensity", "ips_weight", "MAX_WEIGHT"]

#: Ceiling on the weight a single observation may carry. IPS has unbounded
#: variance as propensity approaches zero: one rare event with p = 0.001 would
#: otherwise move a posterior by 1000 plays' worth. Clipping trades a little
#: bias for a variance that does not destroy the estimate — the standard
#: bias-variance trade in clipped IPS.
MAX_WEIGHT = 10.0

#: Propensities below this are treated as this value before inversion.
_MIN_PROPENSITY = 1.0 / MAX_WEIGHT

#: Monte Carlo draws. 2000 puts the standard error of a mid-range propensity
#: near 0.01, which is finer than the weight clipping can express.
_DRAWS = 2000


@dataclass(frozen=True)
class PropensityEstimate:
    """A propensity and whether it was actually derived from anything."""

    propensity: float
    weight: float
    corrected: bool
    competitors: int = 0


def estimate_propensity(
    arm: tuple[float, float],
    competitors: list[tuple[float, float]],
    *,
    draws: int = _DRAWS,
    rng: random.Random | None = None,
) -> float:
    """P(this arm's Thompson draw is the largest), by Monte Carlo.

    ``arm`` and each competitor are ``(alpha, beta)`` posteriors.  With no
    competitors the arm is shown whenever it is considered, so the propensity
    is 1.0 and the correction is a no-op.
    """
    if not competitors:
        return 1.0
    # Fixed seed by default: the same play settled twice must produce the
    # same weight, or a retry would move a posterior differently than the
    # first attempt did.
    generator = rng or random.Random(20260824)
    alpha, beta = _sane(arm)
    others = [_sane(c) for c in competitors]

    wins = 0
    for _ in range(max(1, int(draws))):
        mine = generator.betavariate(alpha, beta)
        if all(mine > generator.betavariate(a, b) for a, b in others):
            wins += 1
    return wins / max(1, int(draws))


def _sane(posterior: tuple[float, float]) -> tuple[float, float]:
    """Beta requires strictly positive parameters; a stored 0 would raise."""
    alpha, beta = posterior
    return (max(float(alpha), 1e-6), max(float(beta), 1e-6))


def ips_weight(
    arm: tuple[float, float] | None,
    competitors: list[tuple[float, float]] | None,
    *,
    draws: int = _DRAWS,
    rng: random.Random | None = None,
) -> PropensityEstimate:
    """Clipped inverse-propensity weight for one observation.

    Returns ``corrected=False`` and ``weight=1.0`` when there is nothing to
    correct against, so a caller can tell an uncorrected update from a
    corrected one that happened to land on 1.0.
    """
    if arm is None or not competitors:
        return PropensityEstimate(propensity=1.0, weight=1.0, corrected=False)

    propensity = estimate_propensity(arm, competitors, draws=draws, rng=rng)
    clipped = max(propensity, _MIN_PROPENSITY)
    weight = min(1.0 / clipped, MAX_WEIGHT)
    return PropensityEstimate(
        propensity=propensity,
        weight=weight,
        corrected=True,
        competitors=len(competitors),
    )
