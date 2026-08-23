# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""An old store must not be locked out by an index on a column it lacks.

``create_all_tables`` runs at every engine start and creates the indexes. One of
them is on ``atomic_facts.pinned``, and that column arrives with M015 — a
*deferred* migration, which runs only after the engine is up.

On a store old enough to predate M015 those two facts formed a deadlock. The
index raised ``no such column: pinned``; an index on a missing column is a hard
error, not a skipped statement, so the whole of ``create_all_tables`` raised and
the engine never started. The deferred pass could not run because the engine
could not start, and the engine could not start because the deferred pass had
not run. ``slm db migrate`` did not break it either — it reports ``Failed=0``
and skips deferred migrations by definition — so there was no documented way out
and the store could not be upgraded at all.

Measured on a real 637 MB store at M013: engine init raised before this change,
and completed in 19.3 s after it, taking the store to M049 with 7,707 facts,
2,590 memories and 848,945 connections all unchanged and ``integrity_check`` ok.

The fix is ordering: a column has to exist before its own index, so the additive
column pass runs before the DDL as well as after it.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage import schema


def _store_predating(column: str, tmp_path):
    """A store that has everything the current schema has, except ``column``.

    Derived from the real schema rather than hand-written. A hand-written table
    is not the store this protects: the first attempt here declared five columns
    and failed on ``fact_type``, because a different index needs that one too.
    The real 637 MB store at M013 has every column of its era and lacks exactly
    one. Deriving it means the fixture cannot drift as the schema grows, and
    cannot accidentally test a shape nobody has on disk.
    """
    import re

    built = tmp_path / "built.db"
    seed = sqlite3.connect(built)
    schema.create_all_tables(seed)
    seed.commit()
    create_sql = seed.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='atomic_facts'"
    ).fetchone()[0]
    seed.close()

    # Delete the one column's line from the real CREATE TABLE text. Rebuilding
    # the declaration from PRAGMA output looks tidier and is not: it has to
    # re-derive NOT NULL and DEFAULT correctly for every column, and got it
    # wrong here on the first attempt.
    without = re.sub(
        rf"^\s*{re.escape(column)}\s+[^\n]*?,?\s*$\n",
        "",
        create_sql,
        count=1,
        flags=re.MULTILINE,
    )
    assert without != create_sql, f"{column} was not found in the schema text"

    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        f"{without};"
        "CREATE TABLE profiles (profile_id TEXT PRIMARY KEY, name TEXT);"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, "
        "fact_type) VALUES (?, ?, ?, ?, ?)",
        (
            "f1", "m1", "default",
            "a memory written long before this column existed", "semantic",
        ),
    )
    conn.commit()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(atomic_facts)")]
    assert column not in columns, "this fixture is only meaningful without it"
    assert "fact_type" in columns, "the fixture must carry the rest of its era"
    return conn, path


class TestAStoreFromBeforeTheColumnStillOpens:
    def test_create_all_tables_does_not_raise_on_a_store_without_pinned(
        self, tmp_path,
    ) -> None:
        """Reverting the fix makes this raise ``no such column: pinned``."""
        conn, _ = _store_predating("pinned", tmp_path)
        try:
            schema.create_all_tables(conn)   # must not raise
            conn.commit()
        finally:
            conn.close()

    def test_the_column_is_there_afterwards(self, tmp_path) -> None:
        """Not raising is not enough — the index needs the column to exist, so
        the column has to be genuinely added, not the index quietly skipped."""
        conn, _ = _store_predating("pinned", tmp_path)
        try:
            schema.create_all_tables(conn)
            conn.commit()
            columns = [r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)")]
            assert "pinned" in columns
            assert "quarantined" in columns, (
                "the column that already worked this way must keep working"
            )
        finally:
            conn.close()

    def test_the_index_that_needed_it_exists(self, tmp_path) -> None:
        """The whole point of the ordering. If the column were added only after
        the DDL, this index would be missing on every upgraded store and every
        lookup by it would fall back to a scan."""
        conn, _ = _store_predating("pinned", tmp_path)
        try:
            schema.create_all_tables(conn)
            conn.commit()
            found = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND name='idx_facts_pinned'"
            ).fetchone()[0]
            assert found, "idx_facts_pinned was not created"
        finally:
            conn.close()

    def test_the_rows_that_were_already_there_survive(self, tmp_path) -> None:
        """An upgrade that opens the store and loses what was in it is worse
        than one that refuses to open."""
        conn, _ = _store_predating("pinned", tmp_path)
        try:
            schema.create_all_tables(conn)
            conn.commit()
            row = conn.execute(
                "SELECT content, pinned FROM atomic_facts WHERE fact_id = 'f1'"
            ).fetchone()
            assert row is not None
            assert row[0] == "a memory written long before this column existed"
            assert row[1] == 0, "the new column should default, not null out"
        finally:
            conn.close()


class TestItIsStillIdempotent:
    def test_running_it_twice_changes_nothing(self, tmp_path) -> None:
        """It runs at every start, so twice has to be the same as once."""
        conn, _ = _store_predating("pinned", tmp_path)
        try:
            schema.create_all_tables(conn)
            conn.commit()
            first = sorted(r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)"))
            schema.create_all_tables(conn)
            conn.commit()
            second = sorted(r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)"))
            assert first == second
        finally:
            conn.close()

    def test_a_fresh_database_is_unaffected(self, tmp_path) -> None:
        """The control. On an empty file the early pass has no tables to alter,
        and the schema must come out exactly as it always did."""
        conn = sqlite3.connect(tmp_path / "fresh.db")
        try:
            schema.create_all_tables(conn)
            conn.commit()
            columns = [r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)")]
            assert "pinned" in columns
            assert "quarantined" in columns
            assert conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND name='idx_facts_pinned'"
            ).fetchone()[0]
        finally:
            conn.close()
