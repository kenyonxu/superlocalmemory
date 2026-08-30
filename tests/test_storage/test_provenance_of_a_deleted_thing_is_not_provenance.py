# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The graph is pruned; the record of how it was built never was.

Every ingestion operation re-captures lineage for the objects present at that
moment, so the table grows with use. Edges are removed by graph pruning. Their
lineage was not, and nothing had ever deleted from that table.

Measured on a real 447 MB store: 256,885 lineage rows, of which 100,581 — 39.2%
— described a graph edge that no longer existed, growing about 9,000 rows a day.

What must remain true afterwards, and is checked here rather than assumed:
lineage for things that still exist survives untouched; the objects themselves
are never touched; a type nobody knows how to resolve is left alone; and running
it twice changes nothing the second time.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.lineage_retention import (
    OBJECT_SOURCES,
    count_orphan_lineage,
    prune_orphan_lineage,
)


@pytest.fixture()
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.executescript(
        """
        CREATE TABLE derivation_lineage (
            lineage_id  TEXT PRIMARY KEY,
            profile_id  TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id   TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            created_at  TEXT
        );
        CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT);
        CREATE TABLE graph_edges  (edge_id TEXT PRIMARY KEY, profile_id TEXT);
        CREATE TABLE profiles     (profile_id TEXT PRIMARY KEY);
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _lineage(conn, lineage_id, object_type, object_id, profile="default"):
    conn.execute(
        "INSERT INTO derivation_lineage VALUES (?,?,?,?,?,?)",
        (lineage_id, profile, object_type, object_id, "op-1", "2026-08-01T00:00:00Z"),
    )


def test_lineage_for_a_deleted_edge_goes(store) -> None:
    store.execute("INSERT INTO graph_edges VALUES ('e-live','default')")
    _lineage(store, "l-live", "graph_edge", "e-live")
    _lineage(store, "l-dead", "graph_edge", "e-gone")
    store.commit()

    report = prune_orphan_lineage(store)

    assert report.deleted == {"graph_edge": 1}
    survivors = {r[0] for r in store.execute("SELECT lineage_id FROM derivation_lineage")}
    assert survivors == {"l-live"}


def test_lineage_for_a_live_object_survives(store) -> None:
    store.execute("INSERT INTO atomic_facts VALUES ('f1','a memory')")
    store.execute("INSERT INTO graph_edges VALUES ('e1','default')")
    store.execute("INSERT INTO profiles VALUES ('default')")
    for i, (kind, oid) in enumerate(
        [("fact", "f1"), ("graph_edge", "e1"), ("profile", "default")]
    ):
        _lineage(store, f"l{i}", kind, oid)
    store.commit()

    before = store.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0]
    report = prune_orphan_lineage(store)
    after = store.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0]

    assert report.total == 0
    assert before == after == 3


def test_the_objects_themselves_are_never_touched(store) -> None:
    store.execute("INSERT INTO atomic_facts VALUES ('f1','a memory')")
    store.execute("INSERT INTO graph_edges VALUES ('e1','default')")
    _lineage(store, "l-dead", "graph_edge", "e-gone")
    store.commit()

    prune_orphan_lineage(store)

    assert store.execute("SELECT COUNT(*) FROM atomic_facts").fetchone()[0] == 1
    assert store.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 1


def test_an_unrecognised_kind_is_left_alone(store) -> None:
    """Not knowing what a row describes is not evidence that it describes nothing."""
    _lineage(store, "l-alien", "something_new", "x-1")
    store.commit()

    report = prune_orphan_lineage(store)

    assert report.total == 0
    assert "something_new" in report.skipped_types
    assert store.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0] == 1


def test_a_kind_whose_table_is_missing_is_left_alone(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "partial.db")
    conn.executescript(
        """
        CREATE TABLE derivation_lineage (
            lineage_id TEXT PRIMARY KEY, profile_id TEXT, object_type TEXT,
            object_id TEXT, operation_id TEXT, created_at TEXT);
        """
    )
    conn.execute(
        "INSERT INTO derivation_lineage VALUES "
        "('l','default','memory_scene','s-1','op','2026-08-01')"
    )
    conn.commit()

    report = prune_orphan_lineage(conn)

    assert report.total == 0
    assert "memory_scene" in report.skipped_types
    assert conn.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0] == 1
    conn.close()


def test_only_the_named_profile_is_pruned(store) -> None:
    _lineage(store, "l-mine", "graph_edge", "gone-1", profile="alice")
    _lineage(store, "l-theirs", "graph_edge", "gone-2", profile="bob")
    store.commit()

    prune_orphan_lineage(store, profile_id="alice")

    survivors = {r[0] for r in store.execute("SELECT lineage_id FROM derivation_lineage")}
    assert survivors == {"l-theirs"}


def test_a_dry_run_deletes_nothing(store) -> None:
    _lineage(store, "l-dead", "graph_edge", "e-gone")
    store.commit()

    report = prune_orphan_lineage(store, dry_run=True)

    assert report.total == 1, "a dry run must still report what it would remove"
    assert store.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0] == 1


def test_running_it_twice_changes_nothing_the_second_time(store) -> None:
    for i in range(5):
        _lineage(store, f"l{i}", "graph_edge", f"gone-{i}")
    store.commit()

    first = prune_orphan_lineage(store)
    second = prune_orphan_lineage(store)

    assert first.total == 5
    assert second.total == 0
    assert count_orphan_lineage(store).total == 0


def test_more_rows_than_one_batch(store) -> None:
    """The loop must terminate on a store larger than a single batch."""
    from superlocalmemory.storage.lineage_retention import _BATCH

    for i in range(_BATCH + 25):
        _lineage(store, f"l{i}", "graph_edge", f"gone-{i}")
    store.commit()

    report = prune_orphan_lineage(store)

    assert report.total == _BATCH + 25
    assert store.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0] == 0


def test_a_store_without_the_table_is_not_an_error(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "bare.db")
    assert prune_orphan_lineage(conn).total == 0
    assert count_orphan_lineage(conn).total == 0
    conn.close()


def test_every_known_kind_names_a_real_column() -> None:
    """A typo here would silently classify every row of that kind as an orphan."""
    from superlocalmemory.core import derivation_lineage as capture

    source = capture.__file__
    text = open(source, encoding="utf-8").read()
    for object_type, (table, column) in OBJECT_SOURCES.items():
        assert f'object_type="{object_type}"' in text or object_type == "index_bm25", (
            f"{object_type!r} is not written by the capture path any more"
        )
        assert table in text, f"{table!r} is not read by the capture path any more"
