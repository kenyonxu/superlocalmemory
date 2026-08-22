"""A per-key cap counts one key, in one workspace, of one kind.

Several tables are bounded by "keep the newest N rows per key". The ids in
those tables are unique inside a workspace and not across the store, so a cap
that partitions on the id alone counts one workspace's rows against another's
allowance, and deletes the loser's provenance. Nothing on a single-workspace
store ever notices, which is why the rule has to hold structurally rather than
by an author remembering to add a column.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

_ROWS_PER_WORKSPACE = 8   # comfortably inside the cap of ten
_CAP = 10


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.migration_runner import apply_all

    config = SLMConfig.load()
    db = DatabaseManager(config.db_path)
    db.initialize(schema)
    apply_all(pathlib.Path(state_path("learning.db")), pathlib.Path(config.db_path))
    db.close()

    conn = sqlite3.connect(str(config.db_path))
    yield conn
    conn.close()


def _lineage(conn, profile_id, object_type, object_id, count, tag):
    for index in range(count):
        conn.execute(
            "INSERT INTO derivation_lineage (profile_id, object_type, object_id, "
            "operation_id, source_status, created_at) VALUES (?,?,?,?,?,?)",
            (profile_id, object_type, object_id, f"{tag}-{index}", "exact",
             f"2026-08-{1 + index % 28:02d}T00:00:00Z"),
        )
    conn.commit()


def _apply(conn):
    from superlocalmemory.storage.retention_policy import (
        REGISTERED_POLICIES,
        apply_policy,
    )

    removed = apply_policy(conn, REGISTERED_POLICIES["derivation_lineage"])
    conn.commit()
    return removed


def _count(conn, **where):
    clause = " AND ".join(f"{column} = ?" for column in where)
    return conn.execute(
        f"SELECT COUNT(*) FROM derivation_lineage WHERE {clause}",
        tuple(where.values()),
    ).fetchone()[0]


def test_two_workspaces_holding_the_same_object_id_keep_all_of_it(store):
    """Sixteen rows under the cap of ten, because they are two keys, not one."""
    _lineage(store, "alpha", "fact", "shared-object", _ROWS_PER_WORKSPACE, "a")
    _lineage(store, "beta", "fact", "shared-object", _ROWS_PER_WORKSPACE, "b")

    assert _apply(store) == 0
    assert _count(store, profile_id="alpha") == _ROWS_PER_WORKSPACE
    assert _count(store, profile_id="beta") == _ROWS_PER_WORKSPACE


def test_two_kinds_of_object_sharing_an_id_keep_all_of_it(store):
    """The id is not unique across kinds either."""
    _lineage(store, "alpha", "fact", "shared-object", _ROWS_PER_WORKSPACE, "f")
    _lineage(store, "alpha", "entity", "shared-object", 9, "e")

    assert _apply(store) == 0
    assert _count(store, profile_id="alpha", object_type="fact") == _ROWS_PER_WORKSPACE
    assert _count(store, profile_id="alpha", object_type="entity") == 9


def test_the_cap_still_bites_inside_one_key(store):
    """The control. Widening the key must not turn the cap off."""
    _lineage(store, "alpha", "fact", "busy-object", 30, "busy")

    assert _apply(store) == 20
    assert _count(store, profile_id="alpha", object_id="busy-object") == _CAP


def test_the_newest_rows_are_the_ones_that_survive(store):
    """A cap that kept the oldest would discard the answer it exists to keep."""
    for index in range(30):
        store.execute(
            "INSERT INTO derivation_lineage (profile_id, object_type, object_id, "
            "operation_id, source_status, created_at) VALUES (?,?,?,?,?,?)",
            ("alpha", "fact", "ordered", f"op-{index:02d}", "exact",
             f"2026-08-{1 + index:02d}T00:00:00Z"),
        )
    store.commit()
    _apply(store)

    survivors = [
        row[0] for row in store.execute(
            "SELECT operation_id FROM derivation_lineage "
            "WHERE object_id = 'ordered' ORDER BY created_at"
        )
    ]
    assert survivors == [f"op-{index:02d}" for index in range(20, 30)]


def test_a_store_without_workspaces_on_the_table_is_still_capped(store):
    """Older stores predate the column. The cap has to work there too."""
    from superlocalmemory.storage.retention_policy import _partition_columns

    store.execute("CREATE TABLE plain_table (key_id TEXT, created_at TEXT)")
    store.commit()

    from superlocalmemory.storage.retention_policy import (
        RetentionKind,
        RetentionPolicy,
    )

    policy = RetentionPolicy(
        table="plain_table",
        kind=RetentionKind.CAP_PER_KEY,
        key_column="key_id",
        cap_per_key=2,
        timestamp_column="created_at",
        reason="a table with no workspace column, for this test only",
    )
    assert _partition_columns(store, policy) == ("key_id",)
