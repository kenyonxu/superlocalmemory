# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Phase 2 end-to-end integration tests — domain tag store/recall flow.

These tests verify the full cross-agent domain sharing pipeline:
- Entity -> domain resolution via domain_mapping table
- _scope_where domain overlap filtering
- Seed data population via M015 post_ddl_hook
- NULL domain_tags invisibility to domain matching
"""

from __future__ import annotations

import pytest


@pytest.fixture
def dbm_with_mappings(tmp_path):
    """DatabaseManager with seed domain_mapping rows."""
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage import schema

    db_path = tmp_path / "test.db"
    dbm = DatabaseManager(db_path)
    dbm.initialize(schema)
    dbm.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')")
    dbm.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('TypeScript', 'frontend')"
    )
    dbm.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('PostgreSQL', 'backend')")
    return dbm


def test_store_with_entity_auto_tags(dbm_with_mappings):
    """Storing content with a known entity auto-tags the fact with domain."""
    from superlocalmemory.storage.database import DatabaseManager

    domains = dbm_with_mappings.resolve_domain_tags(["React"])
    assert domains == ["frontend"]


def test_store_no_matching_entity_no_tags(dbm_with_mappings):
    """Content with unknown entity produces no domain tags."""
    domains = dbm_with_mappings.resolve_domain_tags(["ObscureFramework"])
    assert domains == []


def test_cross_agent_domain_sharing(in_memory_db):
    """Agent B with matching skill sees domain-tagged fact from Agent A."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')")
    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')")
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'docker-compose tip', 'personal', "
        "'[\"devops\"]')"
    )
    in_memory_db.commit()

    where_b, params_b = DatabaseManager._scope_where(
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["devops"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_b}",
        params_b,
    )
    assert any(r["content"] == "docker-compose tip" for r in rows)

    where_b2, params_b2 = DatabaseManager._scope_where(
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["frontend"],
    )
    rows2 = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_b2}",
        params_b2,
    )
    assert not any(r["content"] == "docker-compose tip" for r in rows2)


def test_seed_data_loads_via_post_ddl_hook(in_memory_db):
    """M015 post_ddl_hook seeds domain_mapping correctly."""
    from superlocalmemory.storage.migrations.M015_add_domain_tags import post_ddl_hook

    post_ddl_hook(in_memory_db)

    row = in_memory_db.execute("SELECT COUNT(*) as c FROM domain_mapping").fetchone()
    count = row["c"]
    assert count >= 35, f"Expected at least 35 seed mappings, got {count}"


def test_null_domain_tags_invisible_to_domain_matching(in_memory_db):
    """Facts with domain_tags=NULL are NOT matched by domain overlap."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')")
    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')")
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f1', 'm1', 'a', 'untagged fact', 'personal', NULL)"
    )
    in_memory_db.commit()

    where, params = DatabaseManager._scope_where(
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["frontend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where}",
        params,
    )
    assert not any(r["content"] == "untagged fact" for r in rows)


def test_domain_and_shared_and_domain_overlap_all_visible(in_memory_db):
    """All three visibility paths (personal, shared_with, domain overlap) coexist."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')")
    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')")
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'a', 'src', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m3', 'a', 'src', 'personal')"
    )
    # Fact shared explicitly with B
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f1', 'm1', 'a', 'shared fact', 'personal', '[\"b\"]')"
    )
    # Fact visible via domain overlap
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, domain_tags) "
        "VALUES ('f2', 'm2', 'a', 'domain fact', 'personal', '[\"backend\"]')"
    )
    # Fact with both shared_with and domain_tags
    in_memory_db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, scope, shared_with, domain_tags) "
        "VALUES ('f3', 'm3', 'a', 'both fact', 'personal', '[\"b\"]', '[\"backend\"]')"
    )
    in_memory_db.commit()

    where, params = DatabaseManager._scope_where(
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["backend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where}",
        params,
    )
    contents = [r["content"] for r in rows]
    assert "shared fact" in contents
    assert "domain fact" in contents
    assert "both fact" in contents


def test_seed_covers_all_major_domains(in_memory_db):
    """Seed data covers at least frontend, backend, devops, mobile, data domains."""
    from superlocalmemory.storage.migrations.M015_add_domain_tags import post_ddl_hook

    post_ddl_hook(in_memory_db)

    rows = in_memory_db.execute("SELECT DISTINCT domain FROM domain_mapping")
    domains = {r["domain"] for r in rows}
    for expected in ("frontend", "backend", "devops", "mobile", "data"):
        assert expected in domains, f"Missing domain '{expected}' in seed data"
