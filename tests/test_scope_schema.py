"""Verify scope/shared_with columns exist on core tables after schema creation."""

import sqlite3

import pytest

SCOPE_TABLES = [
    "memories",
    "atomic_facts",
    "canonical_entities",
    "graph_edges",
    "temporal_events",
]


@pytest.fixture
def fresh_db():
    from superlocalmemory.storage import schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create_all_tables(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize("table", SCOPE_TABLES)
def test_scope_column_exists(fresh_db, table):
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "scope" in col_names, f"{table} missing 'scope' column. Got: {col_names}"


@pytest.mark.parametrize("table", SCOPE_TABLES)
def test_shared_with_column_exists(fresh_db, table):
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "shared_with" in col_names, f"{table} missing 'shared_with' column. Got: {col_names}"


def test_scope_default_is_personal(fresh_db):
    fresh_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('alice', 'Alice')")
    fresh_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', 'alice', 'test memory')"
    )
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f1', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute("SELECT scope FROM atomic_facts WHERE fact_id='f1'").fetchone()
    assert row["scope"] == "personal"


def test_shared_with_default_is_null(fresh_db):
    fresh_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('alice', 'Alice')")
    fresh_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', 'alice', 'test memory')"
    )
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f2', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute("SELECT shared_with FROM atomic_facts WHERE fact_id='f2'").fetchone()
    assert row["shared_with"] is None
