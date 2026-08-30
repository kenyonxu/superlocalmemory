# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The highest-trusted agent in a real store was nobody in particular.

Trust is a live control, not a statistic. It gates writes at 0.3 and deletes at
0.5, and a fact with no trust record of its own inherits the trust of whoever
created it, which then multiplies its ranking by up to 1.5.

On a real store the ``unknown`` bucket had 1,708 pieces of evidence and a trust
of 1.000 — higher than any named agent. That figure was the sum of everybody's
behaviour, handed to whoever arrived next without a name: 39 facts were being
ranked at maximum promotion for having no author, and an unidentified caller
cleared both gates by the widest margin available.

Pinning the catch-all to the prior changes nothing for a legitimate anonymous
caller — 0.5 still clears 0.3 and 0.5 — and removes the escalation.
"""

from __future__ import annotations

import pytest

from superlocalmemory.trust.gate import TrustGate
from superlocalmemory.trust.scorer import (
    ANONYMOUS_IDENTITIES,
    TrustScorer,
    is_anonymous,
)

NEUTRAL = 0.5


class _MemoryDB:
    """A trust table in a dict, with the row shape the scorer expects."""

    def __init__(self) -> None:  # noqa: D107 - see class docstring
        self.rows: dict[tuple[str, str, str], tuple[float, int]] = {}
        self.writes: list[str] = []

    def execute(self, sql: str, params: tuple = ()):
        if sql.strip().upper().startswith("SELECT TRUST_SCORE"):
            key = (params[0], params[1], params[2])
            if key not in self.rows:
                return []
            score, count = self.rows[key]
            return [{"trust_score": score, "evidence_count": count}]
        if "FROM provenance" in sql:
            return []
        if sql.strip().upper().startswith(("INSERT", "UPDATE", "REPLACE")):
            # Record it. Asserting on a dict the fake never fills would pass
            # whether or not the code persisted anything.
            self.writes.append(sql)
            return []
        return []


@pytest.mark.parametrize("name", sorted(ANONYMOUS_IDENTITIES))
def test_every_catch_all_reads_as_the_prior(name: str) -> None:
    db = _MemoryDB()
    # However glowing the record written against the bucket...
    db.rows[("agent", name, "default")] = (1.0, 1708)
    assert TrustScorer(db).get_agent_trust(name, "default") == NEUTRAL


@pytest.mark.parametrize("name", ["UNKNOWN", " unknown ", "Anonymous"])
def test_case_and_padding_do_not_smuggle_it_through(name: str) -> None:
    db = _MemoryDB()
    db.rows[("agent", name, "default")] = (1.0, 1708)
    assert TrustScorer(db).get_agent_trust(name, "default") == NEUTRAL


def test_the_write_recorder_actually_records() -> None:
    """The control for the test above: a named agent DOES write."""
    db = _MemoryDB()
    TrustScorer(db).record_signal("claude", "default", "store_success")
    assert db.writes, "the fake never observed a write, so the assertion is dead"


def test_a_named_agent_keeps_its_record() -> None:
    db = _MemoryDB()
    db.rows[("agent", "claude", "default")] = (0.857, 5)
    trust = TrustScorer(db).get_agent_trust("claude", "default")
    assert trust != NEUTRAL, "a real identity must still carry its own history"


def test_nothing_accumulates_against_the_catch_all() -> None:
    db = _MemoryDB()
    scorer = TrustScorer(db)
    for _ in range(50):
        scorer.record_signal("unknown", "default", "store_success")
    assert scorer.get_agent_trust("unknown", "default") == NEUTRAL
    assert db.writes == [], (
        f"a signal was persisted against a bucket that names nobody: {db.writes[:2]}"
    )


def test_an_anonymous_caller_can_still_write_and_delete() -> None:
    """The point is to remove an escalation, not to lock anybody out."""
    gate = TrustGate(TrustScorer(_MemoryDB()))
    assert gate.write_threshold <= NEUTRAL
    assert gate.delete_threshold <= NEUTRAL
    gate.check_write("unknown", "default")     # must not raise
    gate.check_delete("unknown", "default")    # must not raise


def test_the_catch_all_no_longer_outranks_a_real_agent() -> None:
    db = _MemoryDB()
    db.rows[("agent", "unknown", "default")] = (1.0, 1708)
    db.rows[("agent", "claude", "default")] = (0.857, 5)
    scorer = TrustScorer(db)
    assert scorer.get_agent_trust("unknown", "default") < scorer.get_agent_trust(
        "claude", "default"
    ), "being anonymous still buys more trust than being known"


@pytest.mark.parametrize(
    ("name", "anonymous"),
    [
        ("unknown", True), ("", True), ("   ", True), ("None", True),
        ("claude", False), ("mcp_client", False),
        ("daemon-capability:abc123", False), ("codex", False),
    ],
)
def test_which_names_count_as_nobody(name: str, anonymous: bool) -> None:
    assert is_anonymous(name) is anonymous
