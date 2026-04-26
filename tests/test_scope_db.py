# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Test scope-aware database queries."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def scope_db():
    """In-memory DB with scope columns and test data."""
    from superlocalmemory.storage import schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create_all_tables(conn)
    conn.commit()

    # Ensure profiles exist (FK constraint)
    conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('alice', 'Alice')")
    conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('bob', 'Bob')")
    conn.commit()

    # Personal for alice
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'alice', 'alice personal', 'personal')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f1', 'm1', 'alice', 'alice fact', 'personal')"
    )
    # Personal for bob
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'bob', 'bob personal', 'personal')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f2', 'm2', 'bob', 'bob fact', 'personal')"
    )
    # Global
    conn.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m3', 'alice', 'shared globally', 'global')"
    )
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f3', 'm3', 'alice', 'global fact', 'global')"
    )
    # Shared: alice shares with bob
    conn.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f4', 'm1', 'alice', 'shared to bob', 'personal', '[\"bob\"]')"
    )
    conn.commit()
    yield conn
    conn.close()


def _make_db(conn: sqlite3.Connection):
    """Create a DatabaseManager that uses the given connection for testing."""
    from superlocalmemory.storage.database import DatabaseManager

    db = DatabaseManager.__new__(DatabaseManager)
    db._lock = __import__("threading").Lock()
    db._txn_conn = None

    # Patch _connect to return the test connection so execute() works
    db._connect = lambda: conn  # type: ignore[method-assign]
    return db


class TestScopeWhereHelper:
    """Unit tests for the _scope_where static method."""

    def test_personal_only(self):
        from superlocalmemory.storage.database import DatabaseManager

        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="personal",
            include_global=False,
            include_shared=False,
        )
        assert params == ["alice"]
        assert "(scope = 'personal' AND profile_id = ?)" in sql

    def test_personal_with_global(self):
        from superlocalmemory.storage.database import DatabaseManager

        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="personal",
            include_global=True,
            include_shared=False,
        )
        assert params == ["alice"]
        assert "scope = 'global'" in sql

    def test_personal_with_shared(self):
        from superlocalmemory.storage.database import DatabaseManager

        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="personal",
            include_global=False,
            include_shared=True,
        )
        # Two params: one for personal WHERE, one for shared_with subquery
        assert len(params) == 2
        assert "json_each(shared_with)" in sql

    def test_all_three(self):
        from superlocalmemory.storage.database import DatabaseManager

        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="personal",
            include_global=True,
            include_shared=True,
        )
        assert len(params) == 2
        assert " OR " in sql
        assert "scope = 'global'" in sql
        assert "json_each(shared_with)" in sql

    def test_table_alias(self):
        from superlocalmemory.storage.database import DatabaseManager

        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="personal",
            include_global=False,
            include_shared=False,
            table_alias="f",
        )
        assert "f.scope" in sql
        assert "f.profile_id" in sql

    def test_fallback_when_no_conditions(self):
        from superlocalmemory.storage.database import DatabaseManager

        # scope="" with both False should still produce a valid clause
        sql, params = DatabaseManager._scope_where(
            "alice",
            scope="",
            include_global=False,
            include_shared=False,
        )
        assert params == ["alice"]
        assert "profile_id = ?" in sql


class TestGetAllFacts:
    """Tests for get_all_facts with scope filtering."""

    def test_personal_only(self, scope_db):
        """Personal scope: alice sees only her own facts, not bob's."""
        db = _make_db(scope_db)
        facts = db.get_all_facts(
            "alice", scope="personal", include_global=False, include_shared=False
        )
        fact_ids = {f.fact_id for f in facts}
        assert "f1" in fact_ids
        assert "f2" not in fact_ids
        assert "f3" not in fact_ids
        assert "f4" in fact_ids

    def test_with_global(self, scope_db):
        """Personal + global: alice sees her own + global facts."""
        db = _make_db(scope_db)
        facts = db.get_all_facts(
            "alice", scope="personal", include_global=True, include_shared=False
        )
        fact_ids = {f.fact_id for f in facts}
        assert "f1" in fact_ids
        assert "f2" not in fact_ids
        assert "f3" in fact_ids
        assert "f4" in fact_ids

    def test_with_shared(self, scope_db):
        """Personal + shared: bob sees his own + alice's shared_with=bob."""
        db = _make_db(scope_db)
        facts = db.get_all_facts("bob", scope="personal", include_global=False, include_shared=True)
        fact_ids = {f.fact_id for f in facts}
        assert "f1" not in fact_ids
        assert "f2" in fact_ids
        assert "f3" not in fact_ids
        assert "f4" in fact_ids

    def test_invisible_across_profiles(self, scope_db):
        """Bob cannot see alice's personal facts even with include_global."""
        db = _make_db(scope_db)
        facts = db.get_all_facts("bob", scope="personal", include_global=True, include_shared=False)
        fact_ids = {f.fact_id for f in facts}
        assert "f1" not in fact_ids

    def test_backward_compat_defaults(self, scope_db):
        """Default params (include_global=True, include_shared=True) see everything visible."""
        db = _make_db(scope_db)
        facts = db.get_all_facts("bob")
        fact_ids = {f.fact_id for f in facts}
        # bob's own + global + shared from alice
        assert "f2" in fact_ids
        assert "f3" in fact_ids
        assert "f4" in fact_ids
        # alice's personal-only fact should NOT be visible
        assert "f1" not in fact_ids


class TestGetFactCount:
    """Tests for get_fact_count with scope filtering."""

    def test_personal_only(self, scope_db):
        db = _make_db(scope_db)
        count = db.get_fact_count(
            "alice", scope="personal", include_global=False, include_shared=False
        )
        assert count == 2  # f1, f4

    def test_with_global(self, scope_db):
        db = _make_db(scope_db)
        count = db.get_fact_count(
            "alice", scope="personal", include_global=True, include_shared=False
        )
        assert count == 3  # f1, f3, f4

    def test_bob_with_shared(self, scope_db):
        db = _make_db(scope_db)
        count = db.get_fact_count(
            "bob", scope="personal", include_global=False, include_shared=True
        )
        assert count == 2  # f2, f4


class TestGetFactsByType:
    """Tests for get_facts_by_type with scope filtering."""

    def test_scope_filters_by_type(self, scope_db):
        db = _make_db(scope_db)
        # All test facts are semantic type
        facts = db.get_facts_by_type(
            __import__("superlocalmemory.storage.models", fromlist=["FactType"]).FactType.SEMANTIC,
            "alice",
            scope="personal",
            include_global=False,
            include_shared=False,
        )
        fact_ids = {f.fact_id for f in facts}
        assert "f1" in fact_ids
        assert "f2" not in fact_ids
        assert "f3" not in fact_ids


class TestRowToFactScope:
    """Tests that _row_to_fact correctly maps scope and shared_with."""

    def test_scope_default(self, scope_db):
        db = _make_db(scope_db)
        fact = db.get_all_facts(
            "alice", scope="personal", include_global=False, include_shared=False
        )
        f1 = next((f for f in fact if f.fact_id == "f1"), None)
        assert f1 is not None
        assert f1.scope == "personal"

    def test_shared_with_none_for_unshared(self, scope_db):
        db = _make_db(scope_db)
        fact = db.get_all_facts(
            "alice", scope="personal", include_global=False, include_shared=False
        )
        f1 = next((f for f in fact if f.fact_id == "f1"), None)
        assert f1 is not None
        assert f1.shared_with is None

    def test_shared_with_parsed(self, scope_db):
        db = _make_db(scope_db)
        fact = db.get_all_facts(
            "alice", scope="personal", include_global=False, include_shared=False
        )
        f4 = next((f for f in fact if f.fact_id == "f4"), None)
        assert f4 is not None
        assert f4.shared_with == ["bob"]
