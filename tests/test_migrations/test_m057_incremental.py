# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Bound temporal write work and preserve SQLite replacement semantics."""

import sqlite3
import time
from contextlib import closing

import pytest

from superlocalmemory.storage.migrations import M057_temporal_retrieval_version as migration
from tests.test_storage._upgrade_store import current_store


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE atomic_facts(fact_id TEXT PRIMARY KEY, profile_id TEXT,
            scope TEXT, shared_with TEXT, created_at TEXT);
        CREATE TABLE temporal_events(event_id TEXT PRIMARY KEY, fact_id TEXT,
            referenced_date TEXT, observation_date TEXT, interval_start TEXT,
            interval_end TEXT, description TEXT);
    """)
    return conn


def _insert(conn, count, offset=0):
    conn.executemany("INSERT INTO temporal_events(event_id,referenced_date) VALUES (?,'now')",
                     ((str(i),) for i in range(offset, offset + count)))


def _measure(conn):
    steps = []
    conn.set_progress_handler(lambda: steps.append(1) and 0, 1000)
    started = time.perf_counter()
    _insert(conn, 5000, offset=10000)
    elapsed = time.perf_counter() - started
    conn.set_progress_handler(None, 0)
    return elapsed, len(steps)


@pytest.mark.parametrize("existing", [0, 5000])
def test_5000_dynamic_inserts_stay_near_base_write_cost(existing):
    with closing(_connection()) as base, closing(_connection()) as migrated:
        _insert(base, existing)
        _insert(migrated, existing)
        migration.apply(migrated)
        base_time, base_work = _measure(base)
        elapsed, work = _measure(migrated)
        assert work <= base_work * 10, (base_work, work)
        assert elapsed <= max(0.2, base_time * 10), (base_time, elapsed)
        count = migrated.execute("SELECT uncacheable FROM temporal_retrieval_version").fetchone()
        assert count == (existing + 5000,)


@pytest.mark.parametrize("recursive", [0, 1])
def test_counter_tracks_replace_ignore_update_delete_and_rollback(recursive):
    with closing(_connection()) as conn:
        conn.execute(f"PRAGMA recursive_triggers={recursive}")
        migration.apply(conn)
        statements = [
            ("INSERT INTO temporal_events(event_id,referenced_date) VALUES ('a','now')", 1),
            ("INSERT OR REPLACE INTO temporal_events VALUES ('a',NULL,'now',NULL,NULL,NULL,'')", 1),
            ("INSERT OR IGNORE INTO temporal_events(event_id,referenced_date) "
             "VALUES ('a','iso')", 1),
            ("UPDATE temporal_events SET referenced_date='2025-03-15' WHERE event_id='a'", 0),
            ("UPDATE temporal_events SET referenced_date=NULL,observation_date='subsec'", 1),
            ("UPDATE temporal_events SET observation_date=NULL,interval_start='now'", 1),
            ("UPDATE temporal_events SET event_id='b',description='updated'", 1),
            ("INSERT INTO temporal_events(event_id,referenced_date) VALUES ('a','now')", 2),
            ("UPDATE OR REPLACE temporal_events SET event_id='b' WHERE event_id='a'", 1),
            ("DELETE FROM temporal_events", 0),
        ]
        for sql, expected in statements:
            conn.execute(sql)
            count = conn.execute("SELECT uncacheable FROM temporal_retrieval_version").fetchone()
            assert count == (expected,), sql
        conn.commit()
        version = conn.execute("SELECT version FROM temporal_retrieval_version").fetchone()
        _insert(conn, 2)
        conn.rollback()
        assert conn.execute("SELECT version FROM temporal_retrieval_version").fetchone() == version
        assert conn.execute("SELECT uncacheable FROM temporal_retrieval_version").fetchone() == (0,)


def test_repair_upgrades_a_complete_but_recounting_trigger():
    with closing(_connection()) as conn:
        migration.apply(conn)
        conn.execute("DROP TRIGGER trg_temporal_slice_insert")
        conn.executescript("""
            CREATE TRIGGER trg_temporal_slice_insert AFTER INSERT ON temporal_events BEGIN
                UPDATE temporal_retrieval_version SET version=version+1,
                    uncacheable=(SELECT COUNT(*) FROM temporal_events
                                 WHERE referenced_date='now') WHERE id=1;
            END;
        """)
        _insert(conn, 3)
        assert not migration.verify(conn), "trigger names alone cannot certify the new algorithm"
        migration.repair(conn)
        assert migration.verify(conn)
        assert conn.execute("SELECT uncacheable FROM temporal_retrieval_version").fetchone() == (3,)
        conn.execute("DELETE FROM temporal_events WHERE event_id='0'")
        assert conn.execute("SELECT uncacheable FROM temporal_retrieval_version").fetchone() == (2,)


@pytest.mark.parametrize("original", [
    "1b33a4fa21a72714a8e6e95deb4509e644ce4410629ea2152d0deb6b903ab194",
    "79999b6dfcc6cec4250639b9db5f72da02063155bc6b9c4ec32600b9bccbff86",
])
def test_runner_repairs_the_original_wp4b_ddl_hash(tmp_path, original):
    from superlocalmemory.storage import migration_runner as mr

    learning, memory = current_store(tmp_path)
    with closing(sqlite3.connect(memory)) as conn, conn:
        conn.execute("DROP TABLE temporal_uncacheable_events")
        conn.execute("UPDATE migration_log SET ddl_sha256=? WHERE name=?",
                     (original, migration.NAME))
    applied = []
    for run in (mr.apply_all, mr.apply_deferred):
        result = run(learning, memory)
        assert result["failed"] == [], result["details"]
        applied.extend(result["applied"])
    assert migration.NAME in applied
    with closing(sqlite3.connect(memory)) as conn:
        assert migration.verify(conn)


def test_failed_repair_keeps_prior_triggers_and_counter(monkeypatch):
    with closing(_connection()) as conn:
        conn.isolation_level = None  # The runner's connection mode.
        migration.apply(conn)
        _insert(conn, 3)
        before = conn.execute("SELECT * FROM temporal_retrieval_version").fetchone()

        def failed_recount(connection):
            connection.execute("DELETE FROM temporal_uncacheable_events")
            raise sqlite3.OperationalError("synthetic interrupted repair")

        monkeypatch.setattr(migration, "_recount", failed_recount)
        with pytest.raises(sqlite3.OperationalError, match="interrupted repair"):
            migration.repair(conn)
        assert migration.verify(conn)
        assert conn.execute("SELECT * FROM temporal_retrieval_version").fetchone() == before
        assert conn.execute("SELECT COUNT(*) FROM temporal_uncacheable_events").fetchone() == (3,)
