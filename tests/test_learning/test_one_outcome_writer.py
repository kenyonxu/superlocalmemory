# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The canonical outcome writer (BRN-05) and exactly-once settlement (BRN-11).

Real schemas from the migrations; no mocks of the code under test.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from superlocalmemory.learning.bandit import ContextualBandit, close_threadlocal_conn
from superlocalmemory.learning.outcome_events import (
    OutcomeReport,
    UnknownMemories,
    record_outcome_event,
    write_outcome,
)
from superlocalmemory.learning.reward_from_outcomes import settle_from_outcomes

FACT = "f" * 16
OTHER = "e" * 16


@pytest.fixture()
def store(tmp_path: Path):
    from superlocalmemory.learning.database import LearningDatabase
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.migration_runner import apply_all, apply_deferred

    memory, learning = tmp_path / "memory.db", tmp_path / "learning.db"
    with sqlite3.connect(memory) as conn:
        schema.create_all_tables(conn)
        conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) "
                     "VALUES ('default', 'default')")
        for fid in (FACT, OTHER):
            conn.execute(
                "INSERT INTO memories (memory_id, profile_id, content) "
                "VALUES (?, 'default', 'x')", (fid,))
            conn.execute(
                "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, "
                "content) VALUES (?, ?, 'default', ?)", (fid, fid, fid))
    LearningDatabase(learning)
    assert apply_all(learning, memory)["failed"] == []
    assert apply_deferred(learning, memory)["failed"] == []
    yield memory, learning
    close_threadlocal_conn()


def _report(**kw) -> OutcomeReport:
    base = dict(profile_id="default", fact_ids=(FACT,), outcome="success",
                surface="mcp")
    base.update(kw)
    return OutcomeReport(**base)


def _rows(memory: Path) -> list[tuple]:
    with sqlite3.connect(memory) as conn:
        return conn.execute(
            "SELECT outcome_id, reward, settled, settled_at, recall_query_id, "
            "context_json FROM action_outcomes ORDER BY timestamp").fetchall()


@pytest.mark.parametrize("surface", ["mcp", "rest", "cli", "python"])
@pytest.mark.parametrize("verdict,reward", [("success", 1.0), ("partial", 0.5),
                                            ("failure", 0.0)])
def test_every_surface_stores_a_settleable_reward(store, surface, verdict, reward):
    memory, learning = store
    w = write_outcome(_report(outcome=verdict, surface=surface,
                              recall_query_id="q1", agent_id="a1"),
                      memory_db_path=memory, learning_db_path=learning)
    assert w.created and w.attributed and w.reward == reward
    (oid, r, settled, settled_at, qid, ctx), = _rows(memory)
    assert (oid, r, settled, qid) == (w.outcome_id, reward, 1, "q1")
    assert settled_at
    prov = json.loads(ctx)["_provenance"]
    assert prov["surface"] == surface and prov["agent_id"] == "a1"


def test_the_same_report_twice_is_one_event(store):
    memory, learning = store
    first = write_outcome(_report(recall_query_id="q1"), memory_db_path=memory,
                          learning_db_path=learning)
    again = write_outcome(_report(recall_query_id="q1"), memory_db_path=memory,
                          learning_db_path=learning)
    keyed = write_outcome(_report(idempotency_key="k1"), memory_db_path=memory,
                          learning_db_path=learning)
    keyed2 = write_outcome(_report(idempotency_key="k1", outcome="failure"),
                           memory_db_path=memory, learning_db_path=learning)
    bare = write_outcome(_report(surface="rest"), memory_db_path=memory,
                         learning_db_path=learning)
    bare2 = write_outcome(_report(surface="rest"), memory_db_path=memory,
                          learning_db_path=learning)
    assert first.created and not again.created
    assert again.outcome_id == first.outcome_id
    assert keyed.created and not keyed2.created
    assert bare.created and not bare2.created and bare2.outcome_id == bare.outcome_id
    assert len(_rows(memory)) == 3
    changed = write_outcome(_report(recall_query_id="q1", outcome="failure"),
                            memory_db_path=memory, learning_db_path=learning)
    assert changed.created  # a changed verdict is new information


def test_side_effects_run_once_per_event(store):
    memory, learning = store
    for _ in range(3):
        write_outcome(_report(recall_query_id="q9"), memory_db_path=memory,
                      learning_db_path=learning)
    with sqlite3.connect(learning) as conn:
        n = conn.execute("SELECT COUNT(*) FROM learning_feedback "
                         "WHERE signal_type = 'user_positive'").fetchone()[0]
    assert n == 1


def test_unknown_or_foreign_memories_are_refused(store):
    memory, learning = store
    with pytest.raises(UnknownMemories):
        write_outcome(_report(fact_ids=(FACT, "nope")), memory_db_path=memory,
                      learning_db_path=learning)
    with pytest.raises(UnknownMemories):
        write_outcome(_report(profile_id="other"), memory_db_path=memory,
                      learning_db_path=learning)
    with pytest.raises(ValueError):
        write_outcome(_report(outcome="ok"), memory_db_path=memory)
    assert _rows(memory) == []


def test_a_store_without_m006_still_records(tmp_path):
    memory = tmp_path / "old.db"
    with sqlite3.connect(memory) as conn:
        conn.execute("CREATE TABLE action_outcomes (outcome_id TEXT PRIMARY KEY,"
                     " profile_id TEXT, query TEXT, fact_ids_json TEXT,"
                     " outcome TEXT, context_json TEXT, timestamp TEXT)")
        w = record_outcome_event(conn, _report(recall_query_id="q"),
                                 check_memories=False)
    assert w.created and not w.attributed


# ---------------------------------------------------------------------------
# Settlement: one outcome -> one play, durably (BRN-11)
# ---------------------------------------------------------------------------


def _play(learning: Path, qid: str, shown: list[str]) -> int:
    bandit = ContextualBandit(learning, profile_id="default")
    choice = bandit.choose({"query_type": "open_domain", "entity_count": 0}, qid)
    assert bandit.record_shown(choice.play_id, shown)
    return int(choice.play_id)


def _settled(learning: Path) -> dict[str, str | None]:
    with sqlite3.connect(learning) as conn:
        return dict(conn.execute(
            "SELECT query_id, outcome_id FROM bandit_plays"))


def test_the_audit_reproduction_settles_only_the_named_query(store):
    """Three plays showed the same memory; one outcome names query-2."""
    memory, learning = store
    for i in range(3):
        _play(learning, f"query-{i}", [FACT])
    w = write_outcome(_report(recall_query_id="query-2"), memory_db_path=memory,
                      learning_db_path=learning)
    results = [settle_from_outcomes("default", learning, memory) for _ in range(3)]
    assert results == [1, 0, 0]
    assert _settled(learning) == {"query-0": None, "query-1": None,
                                  "query-2": w.outcome_id}


def test_an_unnamed_outcome_is_spent_once_across_passes(store):
    memory, learning = store
    for i in range(3):
        _play(learning, f"q{i}", [FACT])
    write_outcome(_report(surface="rest"), memory_db_path=memory,
                  learning_db_path=learning)
    assert [settle_from_outcomes("default", learning, memory)
            for _ in range(4)] == [1, 0, 0, 0]
    assert sum(1 for v in _settled(learning).values() if v) == 1


def test_the_unique_index_refuses_a_double_spend(store):
    memory, learning = store
    a, b = _play(learning, "qa", [FACT]), _play(learning, "qb", [FACT])
    bandit = ContextualBandit(learning, profile_id="default")
    assert bandit.update(a, 1.0, kind="outcome_reported", outcome_id="oc1")
    assert not bandit.update(b, 1.0, kind="outcome_reported", outcome_id="oc1")
    with sqlite3.connect(learning) as conn:
        plays = conn.execute("SELECT SUM(plays) FROM bandit_arms").fetchone()[0]
        unsettled = conn.execute("SELECT settled_at FROM bandit_plays "
                                 "WHERE play_id = ?", (b,)).fetchone()[0]
    assert plays == 1 and unsettled is None


def test_a_lost_pcos_credit_is_repaired_once(store):
    """Crash between the learning.db commit and the memory.db credit."""
    memory, learning = store
    pid = _play(learning, "qx", [FACT, OTHER])
    w = write_outcome(_report(recall_query_id="qx"), memory_db_path=memory,
                      learning_db_path=learning)
    assert ContextualBandit(learning, profile_id="default").update(
        pid, 1.0, kind="outcome_reported", outcome_id=w.outcome_id)
    for _ in range(3):
        assert settle_from_outcomes("default", learning, memory) == 0
    with sqlite3.connect(memory) as conn:
        rows = dict(conn.execute(
            "SELECT fact_id, play_count FROM fact_outcome_score"))
        ctx = json.loads(conn.execute(
            "SELECT context_json FROM action_outcomes").fetchone()[0])
    assert rows == {FACT: 1}  # named AND shown; OTHER was only shown
    assert ctx["_credited_play"] == pid


def test_an_outcome_outside_the_window_settles_nothing(store):
    memory, learning = store
    _play(learning, "late", [FACT])
    w = write_outcome(_report(recall_query_id="late"), memory_db_path=memory,
                      learning_db_path=learning)
    later = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    with sqlite3.connect(memory) as conn:
        conn.execute("UPDATE action_outcomes SET timestamp = ? "
                     "WHERE outcome_id = ?", (later, w.outcome_id))
    assert settle_from_outcomes("default", learning, memory) == 0
