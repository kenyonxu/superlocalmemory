# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Additive, restartable background description and per-field undo ledger."""

from __future__ import annotations

import sqlite3

NAME = 'M060_memory_description'
DB_TARGET = 'memory'
DOWNGRADE_FLOOR = 51
DDL = '''-- M059 adds non_answer(+_user) and the enrichment runs/changes ledger.
CREATE TABLE IF NOT EXISTS enrichment_runs (
    run_id TEXT PRIMARY KEY, enrichment_version TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'enrichment', status TEXT NOT NULL,
    cursor_rowid INTEGER NOT NULL DEFAULT 0,
    upper_rowid INTEGER NOT NULL, total INTEGER NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0, changed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    backup_point TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrichment_changes (
    change_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
    fact_id TEXT REFERENCES atomic_facts(fact_id) ON DELETE CASCADE,
    memory_id TEXT REFERENCES memories(memory_id) ON DELETE CASCADE,
    target_table TEXT NOT NULL, target_id TEXT NOT NULL, field TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'enrichment', enrichment_version TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES enrichment_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_health_repairs (
    run_id TEXT PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL,
    found INTEGER NOT NULL DEFAULT 0, fixed INTEGER NOT NULL DEFAULT 0,
    error TEXT, native_run_id TEXT, created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_enrichment_changes_run ON enrichment_changes(run_id,change_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_changes_profile ON enrichment_changes(profile_id);
'''


def apply(conn: sqlite3.Connection) -> None:
    for statement in DDL.split(';'):
        if statement.strip():
            conn.execute(statement)
    repairs = {r[1] for r in conn.execute('PRAGMA table_info(memory_health_repairs)')}
    for column, ddl in (('attempts', 'INTEGER NOT NULL DEFAULT 1'), ('last_seen', 'TEXT')):
        if column not in repairs:  # a store that ran an earlier draft of M060
            conn.execute(f'ALTER TABLE memory_health_repairs ADD COLUMN {column} {ddl}')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(atomic_facts)')}
    if columns and 'non_answer' not in columns:
        conn.execute('ALTER TABLE atomic_facts ADD COLUMN non_answer INTEGER DEFAULT 0')
    if columns and 'non_answer_user' not in columns:
        # The user's own "this is an answer" decision; NULL = none (review M1).
        conn.execute('ALTER TABLE atomic_facts ADD COLUMN non_answer_user INTEGER')


def verify(conn: sqlite3.Connection) -> bool:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {r[1] for r in conn.execute('PRAGMA table_info(atomic_facts)')}
    return {'enrichment_runs', 'enrichment_changes'} <= tables and (
        not columns or {'non_answer', 'non_answer_user'} <= columns)


def repair(conn: sqlite3.Connection) -> None:
    apply(conn)
