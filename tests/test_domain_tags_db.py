"""DatabaseManager domain tag tests — resolve_domain_tags + _scope_where."""
import pytest
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage import schema


@pytest.fixture
def dbm_with_mappings(tmp_path):
    """DatabaseManager with seed domain_mapping rows."""
    db_path = tmp_path / "test.db"
    dbm = DatabaseManager(db_path)
    dbm.initialize(schema)
    dbm.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')"
    )
    dbm.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('TypeScript', 'frontend')"
    )
    dbm.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('PostgreSQL', 'backend')"
    )
    return dbm


def test_resolve_domain_tags_empty_input(dbm_with_mappings):
    assert dbm_with_mappings.resolve_domain_tags([]) == []


def test_resolve_domain_tags_single_match(dbm_with_mappings):
    result = dbm_with_mappings.resolve_domain_tags(["React"])
    assert result == ["frontend"]


def test_resolve_domain_tags_multiple_same_domain(dbm_with_mappings):
    result = dbm_with_mappings.resolve_domain_tags(["React", "TypeScript"])
    assert result == ["frontend"]


def test_resolve_domain_tags_cross_domain(dbm_with_mappings):
    result = dbm_with_mappings.resolve_domain_tags(["React", "PostgreSQL"])
    assert set(result) == {"frontend", "backend"}


def test_resolve_domain_tags_no_match(dbm_with_mappings):
    result = dbm_with_mappings.resolve_domain_tags(["Unknown"])
    assert result == []


def test_scope_where_with_skill_tags():
    clause, params = DatabaseManager._scope_where(
        "alice", "personal", False, True, "", skill_tags=["backend", "devops"],
    )
    assert "domain_tags IS NOT NULL" in clause
    assert "json_each" in clause
    assert "backend" in params
    assert "devops" in params


def test_scope_where_without_skill_tags():
    clause, params = DatabaseManager._scope_where(
        "alice", "personal", False, True, "",
    )
    assert "domain_tags" not in clause


def test_domain_recall_visibility(in_memory_db):
    """Agent with matching skill_tags sees domain-tagged facts."""
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'react tip', 'personal', '[\"frontend\"]')"
    )
    in_memory_db.commit()

    clause, params = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["frontend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}", params,
    )
    contents = [r["content"] for r in rows]
    assert "react tip" in contents

    clause2, params2 = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["backend"],
    )
    rows2 = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause2}", params2,
    )
    contents2 = [r["content"] for r in rows2]
    assert "react tip" not in contents2


def test_domain_and_shared_with_coexist(in_memory_db):
    """Both shared_with and domain matching return results."""
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f1', 'm1', 'a', 'shared fact', 'personal', '[\"b\"]')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f2', 'm2', 'a', 'domain fact', 'personal', '[\"backend\"]')"
    )
    in_memory_db.commit()

    clause, params = DatabaseManager._scope_where(
        "b", "personal", False, True, "", skill_tags=["backend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}", params,
    )
    contents = [r["content"] for r in rows]
    assert "shared fact" in contents
    assert "domain fact" in contents
