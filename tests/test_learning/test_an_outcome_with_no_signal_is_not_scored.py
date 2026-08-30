"""A recall nobody engaged with must not be recorded as an average one.

The reward label is built as ``0.5 + bonuses - penalties``, so an outcome
carrying no signals evaluates to exactly 0.5. Persisting that is not a
neutral act: downstream it becomes a mid-strength positive training label
and a Beta update that tightens a posterior around its prior. The bulk
settlement path already declines to score an unsignalled row; the
single-outcome path must behave identically.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from superlocalmemory.learning.reward import EngagementRewardModel


def _store(tmp_path):
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE pending_outcomes (
            outcome_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
            session_id TEXT NOT NULL, recall_query_id TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL, query_text_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL,
            signals_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE action_outcomes (
            outcome_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default',
            query TEXT NOT NULL DEFAULT '', fact_ids_json TEXT NOT NULL DEFAULT '[]',
            outcome TEXT NOT NULL DEFAULT '', context_json TEXT NOT NULL DEFAULT '{}',
            timestamp TEXT NOT NULL DEFAULT (datetime('now')), reward REAL,
            settled INTEGER NOT NULL DEFAULT 0, settled_at TEXT,
            recall_query_id TEXT);
        """
    )
    conn.commit()
    conn.close()
    return db


def _pend(db, signals: dict) -> str:
    oid = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pending_outcomes (outcome_id, profile_id, session_id,"
        " recall_query_id, fact_ids_json, query_text_hash, created_at_ms,"
        " expires_at_ms, signals_json, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,'pending')",
        (oid, "default", "s-1", str(uuid.uuid4()), '["f1"]', "h",
         1, 2, json.dumps(signals)),
    )
    conn.commit()
    conn.close()
    return oid


@pytest.mark.parametrize(
    "signals",
    [{}, {"cite": False, "edit": False, "requery": False, "dwell_ms": 0}],
    ids=["absent", "all-false"],
)
def test_an_unsignalled_outcome_is_finalized_without_a_score(tmp_path, signals):
    db = _store(tmp_path)
    oid = _pend(db, signals)

    EngagementRewardModel(memory_db_path=str(db)).finalize_outcome(outcome_id=oid)

    conn = sqlite3.connect(db)
    scored = conn.execute(
        "SELECT COUNT(*) FROM action_outcomes WHERE outcome_id = ?", (oid,),
    ).fetchone()[0]
    status = conn.execute(
        "SELECT status FROM pending_outcomes WHERE outcome_id = ?", (oid,),
    ).fetchone()[0]
    conn.close()

    assert scored == 0, "an outcome with no signal must not be scored"
    assert status == "settled", "it must still be finalized, not rescanned forever"


def test_a_signalled_outcome_is_still_scored(tmp_path):
    """Non-vacuity: the guard must not suppress real evidence."""
    db = _store(tmp_path)
    oid = _pend(db, {"cite": True})

    EngagementRewardModel(memory_db_path=str(db)).finalize_outcome(outcome_id=oid)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT reward FROM action_outcomes WHERE outcome_id = ?", (oid,),
    ).fetchone()
    conn.close()

    assert row is not None, "a signalled outcome must be scored"
    assert row[0] == pytest.approx(0.9), "cite bonus must still apply"
