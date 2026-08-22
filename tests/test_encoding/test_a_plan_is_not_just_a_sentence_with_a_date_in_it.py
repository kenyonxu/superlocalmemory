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
]


@pytest.mark.parametrize("text", STILL_AHEAD)
def test_something_still_ahead_is_prospective(text: str) -> None:
    assert looks_prospective(text), f"missed a plan: {text!r}"


@pytest.mark.parametrize("text", ALREADY_BEHIND)
def test_something_already_behind_is_not_prospective(text: str) -> None:
    assert not looks_prospective(text), f"filed a finished thing as a plan: {text!r}"


@pytest.mark.parametrize("text", STILL_AHEAD + ALREADY_BEHIND)
def test_both_classifiers_agree(text: str) -> None:
    """One question, one answer, whichever path asked it.

    These were two separate regexes that disagreed: the router's spelling
    classified "the service ends at midnight" as a plan and the extractor's
    did not.
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
