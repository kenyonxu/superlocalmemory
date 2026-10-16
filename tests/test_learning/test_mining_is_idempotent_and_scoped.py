# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Assertion mining: idempotent (BRN-02), scoped (BRN-03), rejections stick.

The audit's fixtures, on the real tool_events / behavioral_assertions schema.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from superlocalmemory.learning.assertion_miner import AssertionMiner
from superlocalmemory.storage.schema_v347 import apply_v347_schema


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "memory.db"
    apply_v347_schema(str(path))
    return path


T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _event(conn, session: str, project: str, tool: str, at: datetime) -> None:
    conn.execute(
        "INSERT INTO tool_events (session_id, profile_id, project_path, "
        "tool_name, event_type, created_at) VALUES (?, 'default', ?, ?, "
        "'invoke', ?)", (session, project, tool, at.isoformat()))


def _workflow_store(db: Path) -> None:
    """Six sessions in /atlas, each Read -> Edit -> Read -> Edit."""
    with sqlite3.connect(db) as conn:
        for s in range(6):
            for i, tool in enumerate(("Read", "Edit", "Read", "Edit")):
                _event(conn, f"atlas-{s}", "/atlas", tool,
                       T0 + timedelta(hours=s, minutes=i))


def _assertions(db: Path) -> dict[str, tuple]:
    with sqlite3.connect(db) as conn:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT id, trigger_condition, action, confidence, evidence_count, "
            "reinforcement_count, project_path, source FROM behavioral_assertions")}


def test_mining_the_same_events_eight_times_changes_nothing(db: Path) -> None:
    _workflow_store(db)
    miner = AssertionMiner(db)
    first = miner.mine("default")
    assert first["created"] > 0
    snapshot = _assertions(db)
    for _ in range(7):
        again = miner.mine("default")
        assert again["created"] == 0 and again["updated"] == 0
    assert _assertions(db) == snapshot
    assert all(row[4] == 0 for row in snapshot.values())  # no auto reinforcement


def test_new_evidence_updates_without_a_nudge(db: Path) -> None:
    _workflow_store(db)
    miner = AssertionMiner(db)
    miner.mine("default")
    with sqlite3.connect(db) as conn:
        _event(conn, "atlas-9", "/atlas", "Read", T0 + timedelta(days=1))
        _event(conn, "atlas-9", "/atlas", "Edit",
               T0 + timedelta(days=1, minutes=1))
    out = miner.mine("default")
    assert out["updated"] >= 1 and out["created"] == 0


def test_interleaved_single_event_sessions_make_no_workflow(db: Path) -> None:
    """The BRN-03 fixture: six Atlas Reads and six Beacon Edits, interleaved,
    each session holding ONE event. No session contains Read -> Edit."""
    with sqlite3.connect(db) as conn:
        for i in range(12):
            atlas = i % 2 == 0
            _event(conn, f"{'atlas' if atlas else 'beacon'}-{i}",
                   "/atlas" if atlas else "/beacon",
                   "Read" if atlas else "Edit", T0 + timedelta(minutes=i))
    AssertionMiner(db).mine("default")
    rules = [r for r in _assertions(db).values() if r[0].startswith("when using")]
    assert rules == []


def test_a_workflow_is_scoped_to_its_project(db: Path) -> None:
    _workflow_store(db)
    AssertionMiner(db).mine("default")
    seq = [r for r in _assertions(db).values() if r[0] == "when using Read"]
    assert seq and all(r[5] == "/atlas" for r in seq)
    assert seq[0][1] == "typically follow with Edit"


def test_events_far_apart_are_not_a_sequence(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        for s in range(6):
            _event(conn, f"s{s}", "/p", "Read", T0 + timedelta(days=s))
            _event(conn, f"s{s}", "/p", "Edit", T0 + timedelta(days=s, hours=5))
    AssertionMiner(db).mine("default")
    assert not [r for r in _assertions(db).values()
                if r[0] == "when using Read"]


def test_a_rejected_rule_is_not_recreated_by_mining(db: Path) -> None:
    from superlocalmemory.mcp.tools_learning import _update_assertion_confidence
    from superlocalmemory.storage.database import DatabaseManager

    _workflow_store(db)
    miner = AssertionMiner(db)
    miner.mine("default")
    target = next(k for k, r in _assertions(db).items()
                  if r[0] == "when using Read")
    mgr = DatabaseManager(db)
    result = {}
    for _ in range(8):
        result = _update_assertion_confidence(mgr, target, reinforce=False,
                                              profile_id="default")
        if result.get("action") == "rejected":
            break
    assert result["action"] == "rejected"
    for _ in range(3):
        miner.mine("default")
    row = _assertions(db)[target]
    assert row[2] == 0.0 and row[6] == "rejected"
    # Only an explicit reinforcement reinstates it.
    _update_assertion_confidence(mgr, target, reinforce=True, profile_id="default")
    assert _assertions(db)[target][6] == "reinstated"
