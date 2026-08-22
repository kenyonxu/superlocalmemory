# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A copy of the words survived in the index that helps find them.

Erasure discovers what to wipe by looking for tables that carry a profile. The
search-expansion index carries a fact instead — it holds the alternate keys a
memory can be found by, keyed on the memory's id — so the sweep never saw it,
and the alternate keys of an erased person's memories stayed in the file after
every trace of the memories themselves was gone.

Reaching it needs a join through the facts, and the join needs the facts to
still be there, so it has to happen before the sweep rather than after. That
ordering is the part worth testing: doing it in the wrong order silently erases
nothing while reporting success.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance


class _SqliteDB:
    """The narrow slice of the database interface erasure uses."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        try:
            return cur.fetchall()
        except sqlite3.ProgrammingError:  # pragma: no cover
            return []


@pytest.fixture()
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.executescript(
        """
        CREATE TABLE profiles (profile_id TEXT PRIMARY KEY);
        CREATE TABLE atomic_facts (
            fact_id TEXT PRIMARY KEY, profile_id TEXT, content TEXT);
        CREATE VIRTUAL TABLE fact_expansion_fts
            USING fts5(fact_id UNINDEXED, alt_keys);
        INSERT INTO profiles VALUES ('alice'),('bob');
        INSERT INTO atomic_facts VALUES
            ('a1','alice','alice mentioned her diagnosis'),
            ('a2','alice','alice lives on Elm Street'),
            ('b1','bob','bob prefers tabs');
        INSERT INTO fact_expansion_fts (fact_id, alt_keys) VALUES
            ('a1','diagnosis condition illness'),
            ('a2','elm street address home'),
            ('b1','tabs indentation whitespace');
        """
    )
    conn.commit()
    yield _SqliteDB(conn)
    conn.close()


def _alt_keys(store) -> set[str]:
    return {r["fact_id"] for r in store.execute("SELECT fact_id FROM fact_expansion_fts")}


def test_the_alternate_keys_go_with_the_memories(store) -> None:
    counts: dict = {}
    GDPRCompliance(store)._erase_fact_keyed_tables("alice", counts)

    assert _alt_keys(store) == {"b1"}, "alice's alternate keys survived erasure"
    assert counts["fact_expansion_fts"] == 2


def test_another_person_keeps_theirs(store) -> None:
    counts: dict = {}
    GDPRCompliance(store)._erase_fact_keyed_tables("alice", counts)

    rows = store.execute("SELECT alt_keys FROM fact_expansion_fts")
    assert [r["alt_keys"] for r in rows] == ["tabs indentation whitespace"]


def test_the_words_are_really_gone_not_just_unreachable(store) -> None:
    """A search must not find them either — this is an index, not a row."""
    GDPRCompliance(store)._erase_fact_keyed_tables("alice", {})

    hits = store.execute(
        "SELECT fact_id FROM fact_expansion_fts WHERE fact_expansion_fts MATCH ?",
        ("diagnosis",),
    )
    assert hits == [], "the erased person's words are still findable by search"


def test_a_profile_with_no_facts_erases_nothing(store) -> None:
    counts: dict = {}
    GDPRCompliance(store)._erase_fact_keyed_tables("carol", counts)
    assert _alt_keys(store) == {"a1", "a2", "b1"}


def test_a_store_without_the_index_is_not_an_error(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "bare.db")
    conn.executescript(
        "CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, profile_id TEXT);"
        "INSERT INTO atomic_facts VALUES ('f','alice');"
    )
    conn.commit()
    counts: dict = {}
    GDPRCompliance(_SqliteDB(conn))._erase_fact_keyed_tables("alice", counts)
    assert "fact_expansion_fts_failed" not in counts
    conn.close()


def test_it_runs_before_the_facts_are_deleted(store) -> None:
    """Doing it after leaves nothing to join on, and reports success anyway."""
    store.execute("DELETE FROM atomic_facts WHERE profile_id = 'alice'")

    counts: dict = {}
    GDPRCompliance(store)._erase_fact_keyed_tables("alice", counts)

    # This is the failure mode being guarded against: with the facts gone, the
    # join finds nothing and the alternate keys survive.
    assert _alt_keys(store) == {"a1", "a2", "b1"}, (
        "this test no longer demonstrates the ordering hazard it exists for"
    )
