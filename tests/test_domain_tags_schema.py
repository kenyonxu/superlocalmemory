# tests/test_domain_tags_schema.py
"""Verify domain_mapping table and domain_tags columns exist after schema creation."""

import sqlite3
import pytest

DOMAIN_TAGS_TABLES = [
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


def test_domain_mapping_table_exists(fresh_db):
    """domain_mapping table must exist after schema creation."""
    rows = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_mapping'"
    ).fetchall()
    assert len(rows) == 1


def test_domain_mapping_columns(fresh_db):
    """domain_mapping must have entity_name and domain columns."""
    rows = fresh_db.execute("PRAGMA table_info(domain_mapping)").fetchall()
    col_names = {r["name"] for r in rows}
    assert "entity_name" in col_names
    assert "domain" in col_names


def test_domain_mapping_primary_key(fresh_db):
    """domain_mapping PK must be (entity_name, domain)."""
    rows = fresh_db.execute("PRAGMA table_info(domain_mapping)").fetchall()
    pk_cols = {r["name"] for r in rows if r["pk"] > 0}
    assert pk_cols == {"entity_name", "domain"}


@pytest.mark.parametrize("table", DOMAIN_TAGS_TABLES)
def test_domain_tags_column_exists(fresh_db, table):
    """Each of the 4 core tables must have a domain_tags column."""
    rows = fresh_db.execute(f"PRAGMA table_info({table})").fetchall()
    col_names = {r["name"] for r in rows}
    assert "domain_tags" in col_names, f"{table} missing 'domain_tags'. Got: {col_names}"


def test_domain_tags_default_is_null(fresh_db):
    """New rows default to domain_tags=NULL."""
    fresh_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('alice', 'Alice')")
    fresh_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content) "
        "VALUES ('m1', 'alice', 'parent memory')"
    )
    fresh_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
        "VALUES ('f1', 'm1', 'alice', 'test')"
    )
    row = fresh_db.execute("SELECT domain_tags FROM atomic_facts WHERE fact_id='f1'").fetchone()
    assert row["domain_tags"] is None
