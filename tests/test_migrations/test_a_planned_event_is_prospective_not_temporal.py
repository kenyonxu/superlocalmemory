# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M046 rebuilds atomic_facts, so these tests run it against real tables.

A migration that drops a table is the one place where a test asserting "the
function was called" is worthless. Every test here builds a store with rows,
indexes, triggers and a search index, runs the migration, and then checks that
what came out the other side is the same data under a different name.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.migrations import (
    M046_prospective_memory_has_its_own_name as M046,
)

# A faithful reduction of the shipped table: TEXT primary key (so rowid is
# implicit, which is what makes the search index fragile), the CHECK that has to
# be widened, and the external-content FTS5 index keyed on rowid.
_CREATE = """
CREATE TABLE atomic_facts (
    fact_id     TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL,
    profile_id  TEXT NOT NULL DEFAULT 'default',
    content     TEXT NOT NULL,
    fact_type   TEXT NOT NULL DEFAULT 'semantic'
                    CHECK (fact_type IN (
                        'episodic', 'semantic', 'opinion', 'temporal'
                    )),
    referenced_date TEXT,
    confidence  REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX idx_af_profile ON atomic_facts (profile_id, fact_type);
CREATE VIRTUAL TABLE atomic_facts_fts USING fts5(
    fact_id UNINDEXED, content,
    content='atomic_facts', content_rowid='rowid'
);
CREATE TRIGGER atomic_facts_fts_insert AFTER INSERT ON atomic_facts BEGIN
    INSERT INTO atomic_facts_fts (rowid, fact_id, content)
    VALUES (new.rowid, new.fact_id, new.content);
END;
CREATE TRIGGER atomic_facts_fts_delete AFTER DELETE ON atomic_facts BEGIN
    INSERT INTO atomic_facts_fts (atomic_facts_fts, rowid, fact_id, content)
    VALUES ('delete', old.rowid, old.fact_id, old.content);
END;
"""

_ROWS = [
    ("f1", "m1", "default", "Dentist appointment on the 4th", "temporal", "2026-09-04", 1.0),
    ("f2", "m1", "default", "Renew the passport before the deadline", "temporal", "2026-10-01", 0.9),
    ("f3", "m2", "default", "Paris is the capital of France", "semantic", None, 1.0),
    ("f4", "m2", "other", "Went to the market on Tuesday", "episodic", None, 0.8),
    ("f5", "m3", "default", "I think the new pipeline is faster", "opinion", None, 0.6),
    ("f6", "m3", "default", "Quarterly review scheduled for March", "temporal", "2026-03-15", 0.7),
]


def _store(path, *, rows=_ROWS) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(_CREATE)
    conn.executemany(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, fact_type, referenced_date, confidence) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


@pytest.fixture
def store(tmp_path):
    conn = _store(tmp_path / "memory.db")
    yield conn
    conn.close()


def _count(conn, sql, *args) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------

def test_every_planned_event_is_renamed_and_nothing_else_is(store):
    before_total = _count(store, "SELECT COUNT(*) FROM atomic_facts")
    before_planned = _count(
        store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='temporal'")
    assert before_planned == 3

    M046.apply(store)

    assert _count(store, "SELECT COUNT(*) FROM atomic_facts") == before_total
    assert _count(
        store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='temporal'") == 0
    assert _count(
        store,
        "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='prospective'",
    ) == before_planned
    # The other three types are untouched.
    for kind, n in (("semantic", 1), ("episodic", 1), ("opinion", 1)):
        assert _count(
            store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_type=?", kind
        ) == n


def test_no_column_and_no_row_content_is_lost(store):
    before = store.execute(
        "SELECT fact_id, memory_id, profile_id, content, referenced_date, "
        "confidence FROM atomic_facts ORDER BY fact_id"
    ).fetchall()

    M046.apply(store)

    after = store.execute(
        "SELECT fact_id, memory_id, profile_id, content, referenced_date, "
        "confidence FROM atomic_facts ORDER BY fact_id"
    ).fetchall()
    assert after == before


def test_the_new_value_can_actually_be_written_afterwards(store):
    """The point of the rebuild. Before it, this INSERT is rejected."""
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, content, fact_type) "
            "VALUES ('new1','m9','Book the flight','prospective')"
        )
    store.rollback()

    M046.apply(store)

    store.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, content, fact_type) "
        "VALUES ('new1','m9','Book the flight','prospective')"
    )
    assert _count(
        store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_id='new1'") == 1


def test_the_old_value_is_rejected_afterwards(store):
    """This is what stops an older process writing the wrong word and winning.

    Without it the constraint would accept both and the old build's memories
    would keep landing under a name nothing looks for.
    """
    M046.apply(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, content, fact_type) "
            "VALUES ('old1','m9','Dentist again','temporal')"
        )


# ---------------------------------------------------------------------------
# The things a rebuild quietly breaks
# ---------------------------------------------------------------------------

def test_the_search_index_still_points_at_the_right_facts(store):
    """The rowid trap.

    The index is external-content FTS5 keyed on rowid, and the primary key is
    TEXT — so the table's rowid is implicit and ``SELECT *`` does not carry it.
    A copy that lets SQLite assign fresh rowids leaves every index entry
    pointing at a different fact. Searches keep working and return the wrong
    rows, and nothing raises.
    """
    M046.apply(store)

    hits = store.execute(
        "SELECT af.fact_id, af.content FROM atomic_facts_fts fts "
        "JOIN atomic_facts af ON af.rowid = fts.rowid "
        "WHERE atomic_facts_fts MATCH 'deadline'"
    ).fetchall()
    assert hits, "the search index found nothing after the rebuild"
    for fact_id, content in hits:
        assert "deadline" in content.lower(), (
            f"the index matched {fact_id} on 'deadline' but its content is "
            f"{content!r} — rowids were reassigned"
        )


def test_rowids_are_preserved_exactly(store):
    before = dict(store.execute("SELECT fact_id, rowid FROM atomic_facts"))
    M046.apply(store)
    after = dict(store.execute("SELECT fact_id, rowid FROM atomic_facts"))
    assert after == before


def test_indexes_and_triggers_come_back(store):
    def names(kind):
        return {
            r[0] for r in store.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name='atomic_facts' "
                "AND type=? AND sql IS NOT NULL", (kind,)
            )
        }

    before_idx, before_trg = names("index"), names("trigger")
    assert before_idx and before_trg

    M046.apply(store)

    assert names("index") == before_idx
    assert names("trigger") == before_trg


def test_the_restored_trigger_still_fires(store):
    """A trigger recreated but broken would only show up on the next write."""
    M046.apply(store)
    store.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, content, fact_type) "
        "VALUES ('t1','m9','A brand new searchable sentence','semantic')"
    )
    hits = store.execute(
        "SELECT fact_id FROM atomic_facts_fts WHERE atomic_facts_fts MATCH 'searchable'"
    ).fetchall()
    assert ("t1",) in hits


# ---------------------------------------------------------------------------
# Safety properties
# ---------------------------------------------------------------------------

def test_running_it_twice_changes_nothing(store):
    M046.apply(store)
    snapshot = store.execute(
        "SELECT fact_id, fact_type, rowid FROM atomic_facts ORDER BY fact_id"
    ).fetchall()

    M046.apply(store)

    assert store.execute(
        "SELECT fact_id, fact_type, rowid FROM atomic_facts ORDER BY fact_id"
    ).fetchall() == snapshot
    assert M046.verify(store)


def test_verify_fails_before_and_passes_after(store):
    assert not M046.verify(store)
    M046.apply(store)
    assert M046.verify(store)


def test_a_store_without_the_table_is_left_alone(tmp_path):
    """A fresh install runs migrations before the schema exists.

    A migration whose verify() demands rows it cannot create fails forever on a
    new machine — the defect a migration in the previous release shipped with.
    """
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        M046.apply(conn)
        assert M046.verify(conn)
    finally:
        conn.close()


def test_a_store_with_no_planned_events_migrates_cleanly(tmp_path):
    conn = _store(tmp_path / "m.db", rows=[r for r in _ROWS if r[4] != "temporal"])
    try:
        M046.apply(conn)
        assert M046.verify(conn)
        assert _count(conn, "SELECT COUNT(*) FROM atomic_facts") == 3
    finally:
        conn.close()


def test_a_failed_rewrite_refuses_rather_than_dropping_the_table(store, monkeypatch):
    """The catastrophic path, forced.

    If the constraint rewrite silently does not take, the staging table carries
    the ORIGINAL constraint and the real table is then dropped — every planned
    event lost to a rejected copy. The migration must stop instead.
    """
    import re as _re

    # A pattern that matches nothing, so the substitution is a no-op.
    monkeypatch.setattr(M046, "_CHECK_RE", _re.compile(r"(ZZZ)(ZZZ)(ZZZ)"))

    with pytest.raises(sqlite3.Error):
        M046._rebuild(store)

    # The original table and every row are still there.
    assert _count(store, "SELECT COUNT(*) FROM atomic_facts") == len(_ROWS)
    assert _count(
        store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='temporal'") == 3


def test_a_short_copy_rolls_back_rather_than_committing(store, monkeypatch):
    """If the copy loses rows, nothing is committed.

    Verified by making the count check see a mismatch, which is the guard that
    stands between a partial copy and a dropped original.
    """
    real_count = M046._count
    calls = {"n": 0}

    def lying_count(conn, sql):
        calls["n"] += 1
        # The second count is the staging table's, taken after the copy.
        if calls["n"] == 2:
            return 1
        return real_count(conn, sql)

    monkeypatch.setattr(M046, "_count", lying_count)

    with pytest.raises(sqlite3.Error):
        M046._rebuild(store)

    assert _count(store, "SELECT COUNT(*) FROM atomic_facts") == len(_ROWS)
    assert _count(
        store, "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='temporal'") == 3


def test_it_puts_the_connections_own_pragma_back(store):
    """The migration borrows the connection; it does not own its settings.

    An unconditional ``foreign_keys=ON`` at the end leaves a caller that had
    them off with them on, which changes how every later statement on that
    connection behaves.
    """
    for original in (True, False):
        store.execute(f"PRAGMA foreign_keys={'ON' if original else 'OFF'}")
        assert bool(store.execute("PRAGMA foreign_keys").fetchone()[0]) is original
        # Re-create the pre-migration shape so _rebuild has work to do.
        store.executescript(
            "DROP TABLE IF EXISTS atomic_facts_fts;"
            "DROP TABLE IF EXISTS atomic_facts;"
        )
        store.executescript(_CREATE)
        store.executemany(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, "
            "fact_type, referenced_date, confidence) VALUES (?,?,?,?,?,?,?)",
            _ROWS,
        )
        store.commit()

        M046.apply(store)

        assert bool(store.execute("PRAGMA foreign_keys").fetchone()[0]) is original, (
            f"foreign_keys was {original} before the migration and is not after"
        )
