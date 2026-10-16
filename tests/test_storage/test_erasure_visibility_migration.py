"""Erasure visibility cache currency is transactional, additive and repairable."""

import sqlite3

import pytest

from superlocalmemory.storage.migrations import M059_erasure_visibility as migration


@pytest.fixture()
def conn():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE projection_tombstones(profile_id TEXT, fact_id TEXT,'
                 'memory_id TEXT, created_at REAL, PRIMARY KEY(profile_id,fact_id))')
    conn.execute('CREATE TABLE fact_search_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,'
                 'profile_id TEXT,fact_id TEXT,what TEXT)')
    conn.execute('CREATE TABLE atomic_facts(profile_id TEXT, fact_id TEXT)')
    conn.executemany('INSERT INTO atomic_facts VALUES (?,?)', [('a','f'),('b','g')])
    conn.commit()
    yield conn
    conn.close()


def test_migration_logs_insert_heal_and_key_update(conn):
    migration.apply(conn)
    migration.apply(conn)
    assert migration.verify(conn)
    conn.execute("INSERT INTO projection_tombstones VALUES ('a','f','m',1)")
    assert conn.execute('SELECT profile_id,fact_id,what FROM fact_search_changes').fetchall() == [
        ('a', 'f', '*')]
    conn.execute("UPDATE projection_tombstones SET profile_id='b',fact_id='g'")
    conn.execute('DELETE FROM projection_tombstones')
    assert conn.execute('SELECT profile_id,fact_id,what FROM fact_search_changes').fetchall() == [
        ('a', 'f', '*'), ('a', 'f', '*'), ('b', 'g', '*'), ('b', 'g', '*')]
    conn.execute('DROP TRIGGER trg_erasure_visibility_insert')
    assert not migration.verify(conn)
    migration.repair(conn)
    assert migration.verify(conn)


def test_migration_does_not_commit_callers_transaction(conn):
    conn.execute('BEGIN')
    migration.apply(conn)
    assert conn.in_transaction
    conn.rollback()
    assert not migration.verify(conn)


def test_schema_without_erasure_or_cache_tables_is_supported(conn):
    conn.execute('DROP TABLE projection_tombstones')
    migration.apply(conn)
    assert migration.verify(conn)
    conn.execute('CREATE TABLE projection_tombstones(profile_id TEXT,fact_id TEXT,memory_id TEXT)')
    conn.execute('DROP TABLE fact_search_changes')
    migration.apply(conn)
    assert migration.verify(conn)
    conn.execute("INSERT INTO projection_tombstones VALUES ('a','f','m')")


def test_removing_a_deleted_facts_tombstone_does_not_recreate_erased_profile_log(conn):
    migration.apply(conn)
    conn.execute("INSERT INTO projection_tombstones VALUES ('a','f','m',1)")
    conn.execute("DELETE FROM atomic_facts WHERE profile_id='a'")
    conn.execute("DELETE FROM fact_search_changes WHERE profile_id='a'")
    conn.execute("DELETE FROM projection_tombstones WHERE profile_id='a'")
    rows = conn.execute(
        "SELECT COUNT(*) FROM fact_search_changes WHERE profile_id='a'").fetchone()
    assert rows[0] == 0
