"""M062: stubs already on disk get the non-answer flag; the user's decision is never touched.

On one real store only 30 of 291 bare "session ended" rows were flagged, and the
hooks' newer shapes were not recognised at all, so recall's newest-first path
was answering from bookkeeping. Runs on a temporary store, never a live one.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage._migration_internals import _MODULES
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migration_runner import DEFERRED_MIGRATIONS
from superlocalmemory.storage.migrations import M060_memory_description
from superlocalmemory.storage.migrations import M062_hook_bookkeeping_is_flagged as M062

STUBS = (
    "[Agentic_official] session ended 2026-10-13 11:01",
    "[Agentic_official] session ended 2026-10-13 09:54 | files: NOTICE, formats.md",
    "session ended 2026-10-12 11:28",
    "File changed: NOTICE",
    "files: index.html, polish-and-review.md",
    "[codex] session ended (exit) at 2026-10-13 11:01 in Agentic_official.",
    "Session ID: 9f8e7d6c5b4a",
)
ANSWERS = (
    "The session ended with a decision to use PostgreSQL.",
    "[Agentic_official] session ended 2026-10-13 11:01 | branch: fix/4.2.2 | recent: 0545bc9a fix",
    "Migrating the search index to the new embedding model.",
    "x" * 2000,
)


@pytest.fixture
def conn(tmp_path):
    manager = DatabaseManager(tmp_path / "memory.db")
    manager.initialize(schema)
    with manager.raw_connection() as c:
        M060_memory_description.apply(c)
    manager.close()
    connection = sqlite3.connect(tmp_path / "memory.db", isolation_level=None)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def _add(conn, fact_id, content, non_answer=0, user=None):
    conn.execute("INSERT INTO memories (memory_id, profile_id, content) VALUES (?, 'default', ?)",
                 (f"m-{fact_id}", content))
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, non_answer, "
        "non_answer_user) VALUES (?, ?, 'default', ?, ?, ?)",
        (fact_id, f"m-{fact_id}", content, non_answer, user))


def _flags(conn):
    return {r["fact_id"]: (r["non_answer"], r["non_answer_user"])
            for r in conn.execute("SELECT fact_id, non_answer, non_answer_user FROM atomic_facts")}


def test_registered_after_m060_with_a_floor_and_no_replay():
    assert _MODULES[M062.NAME] is M062
    migration = next(m for m in DEFERRED_MIGRATIONS if m.name == M062.NAME)
    assert M060_memory_description.NAME in migration.dependencies
    assert M062.DB_TARGET == "memory" and M062.DOWNGRADE_FLOOR == 51
    assert M062.REPAIR_NOT_APPLICABLE.strip()


def test_every_unflagged_stub_is_flagged_and_nothing_else(conn):
    for i, text in enumerate(STUBS):
        _add(conn, f"s{i}", text)
    for i, text in enumerate(ANSWERS):
        _add(conn, f"a{i}", text)
    M062.apply(conn)
    flags = _flags(conn)
    assert all(flags[f"s{i}"] == (1, None) for i in range(len(STUBS))), flags
    assert all(flags[f"a{i}"] == (0, None) for i in range(len(ANSWERS))), flags


def test_a_row_the_user_decided_about_is_never_touched(conn):
    _add(conn, "said-answer", STUBS[0], non_answer=0, user=0)
    _add(conn, "said-stub", STUBS[1], non_answer=1, user=1)
    _add(conn, "plain", STUBS[2])
    M062.apply(conn)
    flags = _flags(conn)
    assert flags["said-answer"] == (0, 0)
    assert flags["said-stub"] == (1, 1)
    assert flags["plain"] == (1, None)


def test_running_it_twice_changes_nothing_more(conn):
    for i, text in enumerate(STUBS):
        _add(conn, f"s{i}", text)
    M062.apply(conn)
    first = _flags(conn)
    before = conn.total_changes
    M062.apply(conn)
    assert _flags(conn) == first
    assert conn.total_changes == before


def test_more_rows_than_one_batch(conn, monkeypatch):
    monkeypatch.setattr(M062, "_BATCH", 3)
    for i in range(10):
        _add(conn, f"s{i}", f"File changed: file{i}.py")
    for i in range(10):
        _add(conn, f"a{i}", f"Reviewing design {i} with the team")
    M062.apply(conn)
    flags = _flags(conn)
    assert sum(v[0] for k, v in flags.items() if k.startswith("s")) == 10
    assert sum(v[0] for k, v in flags.items() if k.startswith("a")) == 0


def test_a_store_without_the_columns_or_the_table_is_left_alone():
    bare = sqlite3.connect(":memory:", isolation_level=None)
    M062.apply(bare)  # no table at all
    bare.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT)")
    bare.execute("INSERT INTO atomic_facts VALUES ('f', 'File changed: NOTICE')")
    M062.apply(bare)  # table without the flag columns (M060 not applied)
    assert bare.execute("SELECT content FROM atomic_facts").fetchone() == ("File changed: NOTICE",)


def test_a_store_with_the_flag_but_no_user_column_is_still_repaired():
    legacy = sqlite3.connect(":memory:", isolation_level=None)
    legacy.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT, "
                   "non_answer INTEGER DEFAULT 0)")
    legacy.execute("INSERT INTO atomic_facts VALUES ('f', 'File changed: NOTICE', 0)")
    M062.apply(legacy)
    assert legacy.execute("SELECT non_answer FROM atomic_facts").fetchone() == (1,)


def test_the_runner_applies_it_to_a_store_that_predates_it(tmp_path):
    from superlocalmemory.storage import migration_runner as mr
    from tests.test_storage._upgrade_store import clear_snapshots, current_store

    learning, memory = current_store(tmp_path)
    with sqlite3.connect(memory) as c:
        c.row_factory = sqlite3.Row
        assert c.execute("SELECT status FROM migration_log WHERE name=?",
                         (M062.NAME,)).fetchone()["status"] == "complete"
        c.execute("DELETE FROM migration_log WHERE name=?", (M062.NAME,))  # a 4.2.1 store
        _add(c, "s0", STUBS[0])
        _add(c, "a0", ANSWERS[2])
    result = mr.apply_deferred(learning, memory)
    clear_snapshots(tmp_path)
    assert M062.NAME in result["applied"] and result["failed"] == [], result
    with sqlite3.connect(memory) as c:
        c.row_factory = sqlite3.Row
        assert _flags(c) == {"s0": (1, None), "a0": (0, None)}
    again = mr.apply_deferred(learning, memory)
    clear_snapshots(tmp_path)
    assert M062.NAME not in again["applied"] and again["failed"] == []
