# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A save's statements cost the same on a large store as on a small one.

Every statement here runs inside a write transaction, holding memory.db's write
lock (the process lock and SQLite's). Measured on a copy of a 27,689-fact store
(14 Oct 2026): the duplicate check in ``matching_fact`` walked every fact of the
profile in ``created_at`` order (1.3-1.7 s per save, idle machine), and the
scene-membership trigger scanned every fact once per scene member (4.26 s for a
1,607-member scene). Under load each became 5-30 s, long enough that mesh
heartbeats failed with "database is locked" and accepted remembers could not
commit. The cost is counted here in SQLite VM steps, not seconds, so the test
decides the same way on a fast and a slow machine.
"""

from __future__ import annotations

import json
import sqlite3

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


def _store(tmp_path, name: str, filler: int) -> DatabaseManager:
    """A store with one source memory and ``filler`` other facts of the profile."""
    db = DatabaseManager(tmp_path / name / "memory.db")
    db.initialize(schema)
    # As an installed store has it: M011's column lets the engine start build
    # the covering recency/visibility indexes, and the planner then sees them.
    db.execute("ALTER TABLE atomic_facts ADD COLUMN archive_status TEXT DEFAULT 'live'")
    db.initialize(schema)
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_facts_recent'")
    db.store_memory(MemoryRecord(memory_id="m", profile_id="default", content="source"))
    db.store_fact(AtomicFact(fact_id="seed", memory_id="m", profile_id="default",
                             content="the seed fact"))
    with db.raw_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM atomic_facts WHERE fact_id='seed'").fetchone())
        cols = list(row)
        sql = (f"INSERT INTO atomic_facts ({','.join(cols)}) "
               f"VALUES ({','.join('?' for _ in cols)})")
        for i in range(filler):
            row.update(fact_id=f"f{i:06d}", content=f"filler fact number {i}",
                       created_at=f"2026-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}.{i:06d}Z")
            conn.execute(sql, [row[c] for c in cols])
    return db


def _steps(db: DatabaseManager, work) -> int:
    """SQLite VM instructions ``work(conn)`` executes (counted in blocks of 100)."""
    count = [0]
    with db.raw_connection() as conn:
        conn.row_factory = sqlite3.Row

        def tick() -> int:
            count[0] += 1
            return 0

        conn.set_progress_handler(tick, 100)
        try:
            work(conn)
        finally:
            conn.set_progress_handler(None, 0)
    return count[0]


class _Conn:
    """The minimum of DatabaseManager that ``matching_fact`` uses."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params).fetchall()


def _duplicate_check(conn: sqlite3.Connection) -> None:
    from superlocalmemory.storage.source_identity import matching_fact

    fact = AtomicFact(memory_id="m", profile_id="default", content="a brand new statement")
    assert matching_fact(_Conn(conn), fact) == []


def test_the_duplicate_check_of_a_save_does_not_grow_with_the_store(tmp_path):
    small = _steps(_store(tmp_path, "small", 200), _duplicate_check)
    large = _steps(_store(tmp_path, "large", 4_000), _duplicate_check)
    # Before: a walk of every fact of the profile, ~20x more steps for 20x facts.
    assert large <= small * 2 + 5, (small, large)


def test_the_duplicate_check_still_finds_the_same_statement_of_the_same_source(tmp_path):
    from superlocalmemory.storage.source_identity import matching_fact

    db = _store(tmp_path, "s", 50)
    with db.raw_connection() as conn:
        conn.row_factory = sqlite3.Row
        same = AtomicFact(memory_id="m", profile_id="default", content="the seed fact")
        assert [r["fact_id"] for r in matching_fact(_Conn(conn), same)] == ["seed"]
        conn.execute(
            "INSERT INTO fact_temporal_validity (fact_id, profile_id, system_expired_at) "
            "VALUES ('seed', 'default', '2026-10-14T00:00:00Z') "
            "ON CONFLICT(fact_id) DO UPDATE SET system_expired_at=excluded.system_expired_at")
        # A retired fact is history: a new statement of it is a new fact.
        assert matching_fact(_Conn(conn), same) == []


def _scene_save(members: list[str]):
    def work(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO memory_scenes (scene_id, profile_id, theme, "
            "fact_ids_json, entity_ids_json, created_at, last_updated) "
            "VALUES ('scene', 'default', 't', ?, '[]', 'now', 'now')",
            (json.dumps(members),))
    return work


def test_saving_a_scene_does_not_scan_every_fact_per_member(tmp_path):
    members = [f"f{i:06d}" for i in range(150)]
    small = _steps(_store(tmp_path, "small", 200), _scene_save(members))
    large = _steps(_store(tmp_path, "large", 4_000), _scene_save(members))
    # Before: (facts x members) comparisons -- 20x the facts, ~20x the steps.
    assert large <= small * 2 + 5, (small, large)


def test_a_scene_still_lists_exactly_its_live_members_in_order(tmp_path):
    db = _store(tmp_path, "s", 10)
    _steps(db, _scene_save(["f000003", "gone", "f000001"]))
    rows = db.execute("SELECT fact_id, position FROM scene_fact_members "
                      "WHERE scene_id='scene' ORDER BY position")
    assert [(r["fact_id"], r["position"]) for r in rows] == [("f000003", 0), ("f000001", 2)]
    db.execute("UPDATE memory_scenes SET fact_ids_json=? WHERE scene_id='scene'",
               (json.dumps(["f000002"]),))
    rows = db.execute("SELECT fact_id FROM scene_fact_members WHERE scene_id='scene'")
    assert [r["fact_id"] for r in rows] == ["f000002"]


def test_a_store_with_the_old_scene_triggers_gets_the_indexed_ones_at_start(tmp_path):
    db = _store(tmp_path, "old", 10)
    old_join = "JOIN atomic_facts AS af"
    with db.raw_connection() as conn:
        for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'trg_scene_fact_members_%'").fetchall():
            conn.execute(f"DROP TRIGGER {name}")
            # The 4.2.4 text, as every installed store has it.
            conn.execute(sql.replace("CROSS JOIN atomic_facts AS af", old_join))
    db.initialize(schema)  # every engine start
    triggers = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                          "AND name LIKE 'trg_scene_fact_members_%'")
    assert len(triggers) == 2
    assert all("CROSS JOIN atomic_facts AS af" in r["sql"] for r in triggers)
