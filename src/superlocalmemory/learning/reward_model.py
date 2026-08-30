# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Turn observed engagement into a reward, or into an honest refusal.

ABSTENTION IS THE POINT
-----------------------
The ladder this replaces always produced a number. When it saw nothing it
produced ``0.5``, and ``0.5`` is not neutral: ``alpha += 0.5`` with
``beta += 0.5`` holds a Beta posterior's mean at exactly 0.5 while shrinking
its variance, so each empty settlement makes an arm *more certain* it is
average and *less* movable by the evidence that finally arrives. Applied often
enough it does not merely fail to learn — it commits, with growing confidence,
to knowing nothing.

So this module returns ``None`` when nothing was observed. An unobserved recall
leaves the posterior untouched and free. Absence of evidence is recorded as
absence of evidence.

THE SCALE
---------
Positive evidence maps into ``(0.5, 1.0]``, a requery to ``0.0``, and nothing to
``None``. Nothing maps *to* 0.5, because that value is reserved for "no
information" and no observation carries that meaning: if it was worth
observing it was worth moving the posterior.

Weights are module constants and deliberately legible rather than fitted. There
is no ground-truth corpus of "was this memory actually useful" to fit against,
and inventing one from the system's own rankings would be the circularity
``propensity.py`` exists to break. They are a documented prior over evidence
strength; the *learning* happens in the posterior these rewards update, not in
the constants.
"""

from __future__ import annotations

from dataclasses import dataclass

from superlocalmemory.learning.engagement_features import EngagementFeatures

__all__ = ["RewardDecision", "score", "REQUERY_REWARD"]

#: A question asked again is the one unambiguous statement that an answer did
#: not serve. It is the only hard zero available.
REQUERY_REWARD = 0.0

#: Evidence weights, strongest first.
#:  - a memory whose content reaches a written artifact was used, not just read
#:  - a follow-up memory overlapping it means the agent built on it
#:  - appearing in any later action is real but weaker: the agent may have been
#:    working on the subject regardless of what was recalled
_W_ARTIFACT = 0.50
_W_FOLLOWUP = 0.30
_W_PRESENCE = 0.20

#: Positive evidence starts just above the reserved no-information value, so
#: the weakest real observation still moves an arm up rather than nowhere.
_POSITIVE_FLOOR = 0.55


@dataclass(frozen=True)
class RewardDecision:
    """A reward, or an abstention, with the reason attached.

    ``reward is None`` means do not update. ``kind`` names what was seen so a
    settled play can be explained after the fact instead of being a bare float.
    """

    reward: float | None
    kind: str
    detail: str = ""

    @property
    def abstained(self) -> bool:
        return self.reward is None


def score(features: EngagementFeatures) -> RewardDecision:
    """Map observations to a reward in [0, 1], or abstain.

    Never raises: a settler running over a month of rows must not stop on one
    malformed observation.
    """
    if features.marker_hit:
        # The agent named the memory outright. Nothing makes it do this, so it
        # is rare, but when it happens there is nothing to infer.
        return RewardDecision(
            reward=1.0,
            kind="proxy_position",
            detail="a recalled fact id appeared in a later tool event",
        )

    if features.requeried:
        return RewardDecision(
            reward=REQUERY_REWARD,
            kind="proxy_requery",
            detail="the same question was asked again inside the window",
        )

    if not features.observed:
        return RewardDecision(
            reward=None,
            kind="unobserved",
            detail=(
                f"no engagement visible in {features.action_count} following "
                "action(s); posterior left untouched"
            ),
        )

    artifact = _clamp(features.artifact_overlap)
    followup = _clamp(features.followup_write_overlap)
    presence = _clamp(features.peak_overlap)

    strength = (
        _W_ARTIFACT * artifact
        + _W_FOLLOWUP * followup
        + _W_PRESENCE * presence
    )
    # strength in [0, 1]; map onto (floor, 1.0].
    reward = _POSITIVE_FLOOR + (1.0 - _POSITIVE_FLOOR) * _clamp(strength)

    if artifact > 0.0:
        kind = "artifact_overlap"
    elif followup > 0.0:
        kind = "followup_overlap"
    else:
        kind = "presence_overlap"

    return RewardDecision(
        reward=round(reward, 6),
        kind=kind,
        detail=(
            f"artifact={artifact:.3f} followup={followup:.3f} "
            f"presence={presence:.3f} on {len(features.matched_fact_ids)} fact(s)"
        ),
    )


def _clamp(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
