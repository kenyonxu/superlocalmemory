# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""A prospective memory is something still ahead, not any sentence mentioning time.

Measured on a real 468 MB store before this rule existed: 869 memories carried
the prospective type and seven of them contained any planning language. The rest
were records of finished work. Two causes, both in the marker patterns: ``by
\\w+`` matched every passive-voice sentence ("was caused **by a** stale DNS
entry") and ``yesterday`` was listed as a marker of something that had not
happened yet.

The pairs below are the shapes that actually appeared. Each "not a plan" case is
a real sentence the old rule filed as one.
"""

from __future__ import annotations

import pytest

from superlocalmemory.encoding.fact_extractor import _classify_sentence
from superlocalmemory.encoding.prospective_markers import looks_prospective
from superlocalmemory.encoding.type_router import TypeRouter
from superlocalmemory.storage.models import AtomicFact, FactType

STILL_AHEAD = [
    "The migration is scheduled for next Tuesday",
    "Deadline for the audit is 2026-09-01",
    "The TLS certificate expires on 2026-09-14",
    "Dentist appointment tomorrow at 10:30",
    "We plan to cut over to the new store next month",
    "The release was rescheduled to next Friday",
    "Renew the domain by end of September",
    "The maintenance window starts on Sunday",
    "The conference is scheduled for next month",
    "The worktrees will be deleted",
    "Remind me to renew the passport on 2026-09-04",
    "Due Friday",
    "The deadline for NeurIPS is May",
    "The deployment of qualixar.com will occur on Monday morning",
]

ALREADY_BEHIND = [
    # Passive voice — the shape that produced most of the 869.
    "The outage was caused by a stale DNS entry",
    "The report was written by the platform team",
    # Explicitly past.
    "We shipped the fix yesterday",
    "The build was fixed after the dependency bump",
    "The meeting on Monday produced three decisions",
    "The session ended at 01:28",
    "The conversation ended at 20:33 local time",
    # A configuration value that happens to use the word expiry.
    "Decided to use JWT with 1h expiry for API auth",
    # A recurring property, not an event.
    "The service ends at midnight",
    # Ordinary statements with no time claim at all.
    "Recall p95 is 1134 ms against a 2000 ms ceiling",
    "Paris is the capital of France",
    # Every case below was filed as a plan by the first version of this rule,
    # which let planning vocabulary win outright. They were found by review,
    # not by the author, which is why the list is kept rather than trimmed:
    # the earlier tests only covered sentences already known to be wrong.
    "The appointment was cancelled yesterday",
    "We missed the deadline last week",
    "The launch date was March 2024",
    "The go-live happened last Tuesday",
    "This will be stored as a semantic fact",
    "The upcoming section covers retrieval",
    "JWT tokens expire on every request",
    "Standup at 09:30 every day",
    "Will the service need a fix?",
    # A calendar date is not a tense. These are the commonest shapes in a real
    # store, and treating a date as forward-looking filed all of them as plans.
    "[codex] session ended (stop) at 2026-08-13 08:32 in memories",
    "The Git repository was updated on 2026-08-03",
    "The transport feature was merged on 2024-04-01",
    "157 files were changed on 2026-08-02",
    "The backup was created on 2026-08-04 at 23:26",
]


@pytest.mark.parametrize("text", STILL_AHEAD)
def test_something_still_ahead_is_prospective(text: str) -> None:
    assert looks_prospective(text), f"missed a plan: {text!r}"


@pytest.mark.parametrize("text", ALREADY_BEHIND)
def test_something_already_behind_is_not_prospective(text: str) -> None:
    assert not looks_prospective(text), f"filed a finished thing as a plan: {text!r}"


@pytest.mark.parametrize(
    "text",
    STILL_AHEAD + ALREADY_BEHIND + [
        "I think we should ship next week",
        "I believe the deadline is 2026-09-01",
        "I prefer to migrate on Monday",
    ],
)
def test_both_classifiers_agree(text: str) -> None:
    """One question, one answer, whichever path asked it.

    These were two separate regexes that disagreed: the router's spelling
    classified "the service ends at midnight" as a plan and the extractor's did
    not. They now share one rule AND ask it in the same order — the extractor
    used to check "is it a plan" before "is it an opinion", so "I think we
    should ship next week" was a plan on one path and an opinion on the other.

    SCOPE: this covers the prospective decision only. The opinion and episodic
    marker sets are still two divergent pairs, and on a real 5,283-memory store
    they disagree 227 times. That is the same defect class, in code this release
    did not touch, and it is recorded rather than quietly implied to be fixed.
    """
    from_extractor = _classify_sentence(text) is FactType.PROSPECTIVE
    router = TypeRouter.__new__(TypeRouter)
    fact = AtomicFact(fact_id="t", memory_id="m", content=text)
    from_router = router._classify_keywords(fact) is FactType.PROSPECTIVE
    assert from_extractor == from_router, (
        f"extractor says prospective={from_extractor}, router says "
        f"{from_router}, for {text!r}"
    )


def test_a_deliberate_asymmetry_is_documented_and_real() -> None:
    """When the evidence is circumstantial, a finished thing wins.

    "starts on Sunday" is a plan; "started on Sunday" is a record. The rule
    keeps the second out at the cost of occasionally missing the first, because
    a plan filed as an ordinary memory is still findable by every other channel
    and a record filed as a plan pollutes the list of what is coming up.
    """
    assert looks_prospective("The migration starts on Sunday")
    assert not looks_prospective("The migration started on Sunday")


RECOGNISED_AND_DELIBERATE_MISSES = [
    # Each of these IS a plan and this rule says it is not. They are listed
    # rather than fixed because every widening that would catch them also
    # re-admits a class of finished work, and a plan filed as an ordinary
    # memory is still found by every retrieval channel — while an ordinary
    # memory filed as a plan is the pollution this exists to remove.
    #
    # A past-tense verb about the DECISION, not about the event:
    "We agreed the freeze starts on Monday",
    "I created a calendar event on Friday at 15:00",
    # A bare clock with no minutes:
    "Let us meet Friday at 3",
]


@pytest.mark.parametrize("text", RECOGNISED_AND_DELIBERATE_MISSES)
def test_the_misses_are_the_ones_we_chose(text: str) -> None:
    """Pinned so a future widening has to come here and argue with the reason.

    If one of these starts passing, that is not automatically wrong — but it
    means the trade was changed, and the change should be deliberate.
    """
    assert not looks_prospective(text), (
        f"{text!r} now classifies as a plan; if that was intended, move it into "
        f"STILL_AHEAD and say what it cost on the other side"
    )


def test_a_recurring_event_is_not_something_coming_up() -> None:
    """A standup every morning belongs in nobody's list of what to expect."""
    assert not looks_prospective("Standup at 09:30 every day")
    assert not looks_prospective("The backup runs nightly at 02:00")
    assert looks_prospective("Standup moved to next Monday")
