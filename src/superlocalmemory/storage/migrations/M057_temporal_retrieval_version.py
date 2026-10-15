# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""M057: invalidate temporal slices on source writes, not access accounting.

Dates remain unrestricted TEXT. Non-ISO values (including SQLite's dynamic
``now`` / ``subsec``) keep using the original SQL on every read. The counter
tracks them without rescanning the event table on each recall.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

NAME = "M057_temporal_retrieval_version"
DB_TARGET = "memory"
#: Additive (a new table, a partial index and triggers). The triggers live in
#: the store, so writes by an older build still bump the version and the slice
#: cache cannot go stale after a downgrade and re-upgrade. Going back to 4.1.18
#: stays possible.
DOWNGRADE_FLOOR = 51


def _unsafe(prefix: str = "") -> str:
    value = f"COALESCE({prefix}referenced_date,{prefix}observation_date,{prefix}interval_start)"
    return (f"CASE WHEN {value} IS NOT NULL AND {value} NOT GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' THEN 1 ELSE 0 END")


def _forget(prefix: str) -> str:
    return f"""
    UPDATE temporal_retrieval_version SET uncacheable=uncacheable-
        EXISTS(SELECT 1 FROM temporal_uncacheable_events WHERE event_id={prefix}event_id)
        WHERE id=1;
    DELETE FROM temporal_uncacheable_events WHERE event_id={prefix}event_id;
    """


def _remember_new() -> str:
    return f"""
    UPDATE temporal_retrieval_version SET version=version+1,
        uncacheable=uncacheable+({_unsafe('NEW.')}) WHERE id=1;
    INSERT OR IGNORE INTO temporal_uncacheable_events
        SELECT NEW.event_id WHERE ({_unsafe('NEW.')})=1;
    """


DDL = f"""
CREATE INDEX IF NOT EXISTS idx_temporal_uncacheable ON temporal_events(event_id)
    WHERE ({_unsafe()})=1;
CREATE TABLE IF NOT EXISTS temporal_retrieval_version (
    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
    uncacheable INTEGER NOT NULL
);
INSERT OR IGNORE INTO temporal_retrieval_version VALUES (1,0,0);
CREATE TABLE IF NOT EXISTS temporal_uncacheable_events (event_id TEXT PRIMARY KEY);
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_insert AFTER INSERT ON temporal_events BEGIN
    {_forget('NEW.')}
    {_remember_new()}
END;
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_delete AFTER DELETE ON temporal_events BEGIN
    {_forget('OLD.')}
    UPDATE temporal_retrieval_version SET version=version+1 WHERE id=1;
END;
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_update AFTER UPDATE ON temporal_events BEGIN
    {_forget('OLD.')}
    {_forget('NEW.')}
    {_remember_new()}
END;
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_fact_delete AFTER DELETE ON atomic_facts BEGIN
    UPDATE temporal_retrieval_version SET version=version+1 WHERE id=1;
END;
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_fact_insert AFTER INSERT ON atomic_facts BEGIN
    UPDATE temporal_retrieval_version SET version=version+1 WHERE id=1;
END;
CREATE TRIGGER IF NOT EXISTS trg_temporal_slice_fact_update
AFTER UPDATE OF fact_id,profile_id,scope,shared_with,created_at ON atomic_facts
WHEN NEW.fact_id IS NOT OLD.fact_id OR NEW.profile_id IS NOT OLD.profile_id
    OR NEW.scope IS NOT OLD.scope
    OR NEW.shared_with IS NOT OLD.shared_with OR NEW.created_at IS NOT OLD.created_at BEGIN
    UPDATE temporal_retrieval_version SET version=version+1 WHERE id=1;
END;
"""

_TRIGGERS = tuple(f"trg_temporal_slice_{suffix}" for suffix in
                  ("insert", "delete", "update", "fact_delete", "fact_insert", "fact_update"))


def _statements() -> list[str]:
    statements, pending = [], ""
    for line in DDL.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    return statements


def _normal(sql: str) -> str:
    return " ".join(sql.replace("IF NOT EXISTS ", "").split()).casefold().rstrip(";")


def _recount(conn: sqlite3.Connection) -> None:
    """Bootstrap / repair only. Row writes use indexed membership deltas.

    Membership survives INSERT OR REPLACE's implicit delete even when SQLite
    suppresses delete triggers (recursive_triggers=OFF, the product default).
    It also handles UPDATE OR REPLACE, ignored inserts and rolled-back writes.
    """
    conn.execute("DELETE FROM temporal_uncacheable_events")
    conn.execute("INSERT INTO temporal_uncacheable_events SELECT event_id "
                 f"FROM temporal_events WHERE ({_unsafe()})=1")
    conn.execute("UPDATE temporal_retrieval_version SET version=version+1,uncacheable="
                 "(SELECT COUNT(*) FROM temporal_uncacheable_events) WHERE id=1")


@contextmanager
def _atomic(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT m057_setup")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO m057_setup")
        raise
    finally:
        conn.execute("RELEASE m057_setup")


def apply(conn: sqlite3.Connection) -> None:
    with _atomic(conn):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"temporal_events", "atomic_facts"} <= tables:
            for statement in _statements():
                conn.execute(statement)
            if "temporal_uncacheable_events" not in tables:
                _recount(conn)


def verify(conn: sqlite3.Connection) -> bool:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"temporal_events", "atomic_facts"} <= tables:
        return True
    triggers = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    expected = {s.split()[5]: _normal(s) for s in _statements() if s.startswith("CREATE TRIGGER")}
    return ({"temporal_retrieval_version", "temporal_uncacheable_events"} <= tables
            and all(_normal(triggers.get(name, "")) == sql for name, sql in expected.items())
            and bool(conn.execute("SELECT 1 FROM sqlite_master "
                                  "WHERE name='idx_temporal_uncacheable'").fetchone())
            and bool(conn.execute("SELECT 1 FROM temporal_retrieval_version WHERE id=1")
                     .fetchone()))


def repair(conn: sqlite3.Connection) -> None:
    with _atomic(conn):
        for name in _TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        apply(conn)
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='temporal_retrieval_version'")
        if exists.fetchone():
            _recount(conn)
