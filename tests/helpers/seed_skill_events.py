# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Seed Skill tool events so the performance miner has something to mine.

The miner needs at least ``MIN_INVOCATIONS`` recorded uses of a skill before it
will write an assertion about it, and a live store had exactly one Skill event
in two thousand tool events. So nothing was ever mined, no assertion with
``category='skill_performance'`` was ever written, and every downstream trigger
that looks for one found nothing — a chain of four apparently-separate blocks
with a single cause at the top.

This builds the input that cause was missing, so the rest of the chain can be
tested without waiting for someone to use the product enough times.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    profile_id TEXT DEFAULT 'default',
    project_path TEXT DEFAULT '',
    tool_name TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'invoke',
    input_summary TEXT DEFAULT '',
    output_summary TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS behavioral_assertions (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL DEFAULT 'default',
    project_path TEXT DEFAULT '',
    trigger_condition TEXT NOT NULL,
    action TEXT NOT NULL,
    category TEXT DEFAULT 'workflow',
    confidence REAL DEFAULT 0.3,
    evidence_fact_ids TEXT DEFAULT '[]',
    evidence_count INTEGER DEFAULT 1,
    reinforcement_count INTEGER DEFAULT 0,
    contradiction_count INTEGER DEFAULT 0,
    last_reinforced_at TEXT,
    last_contradicted_at TEXT,
    source TEXT DEFAULT 'auto',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema(db_path: str | Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def seed_skill_events(
    db_path: str | Path,
    *,
    skill_name: str = "brainstorming",
    invocations: int = 5,
    failures: int = 2,
    profile_id: str = "default",
    session_prefix: str = "seeded",
) -> int:
    """Record ``invocations`` uses of one skill, ``failures`` of them going badly.

    Each use is given its own session and its own hour. Sessions matter because
    the miner traces what happened after a Skill call within a session, and
    spacing matters because two uses of the same skill close together are read
    as a retry — a signal in its own right, and not the one being seeded here.

    Returns the number of events written (one per invocation, plus one trailing
    event per invocation for the miner to read the outcome from).
    """
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    written = 0
    try:
        base = datetime.now(timezone.utc) - timedelta(days=2)
        for i in range(invocations):
            at = base + timedelta(hours=i)
            session = f"{session_prefix}-{i}"
            conn.execute(
                "INSERT INTO tool_events (session_id, profile_id, project_path, "
                "tool_name, event_type, input_summary, output_summary, "
                "duration_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (session, profile_id, "/seeded/project", "Skill", "invoke",
                 json.dumps({"skill": skill_name}), "", 120,
                 at.isoformat()),
            )
            # What happened next, which is what the miner reads the outcome from.
            failed = i < failures
            conn.execute(
                "INSERT INTO tool_events (session_id, profile_id, project_path, "
                "tool_name, event_type, input_summary, output_summary, "
                "duration_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (session, profile_id, "/seeded/project",
                 "Bash", "invoke", "", "error: command failed" if failed else "ok",
                 40, (at + timedelta(seconds=30)).isoformat()),
            )
            written += 2
        conn.commit()
    finally:
        conn.close()
    return written


def count_skill_assertions(
    db_path: str | Path, *, profile_id: str = "default",
) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM behavioral_assertions "
            "WHERE profile_id = ? AND category = 'skill_performance'",
            (profile_id,),
        ).fetchone()[0])
    finally:
        conn.close()
