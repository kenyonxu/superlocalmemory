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


class _MockLLM:
    """Minimal mock for LLMBackbone."""

    def __init__(self):
        self.response = ""
        self.should_raise = False
        self.call_count = 0

    def generate(self, prompt, system="", temperature=None, max_tokens=None):
        self.call_count += 1
        if self.should_raise:
            raise RuntimeError("LLM unavailable")
        return self.response

    def is_available(self):
        return True


@pytest.fixture
def mock_llm():
    return _MockLLM()


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


def test_enrich_fact_llm_classify_on_store(tmp_path, mock_llm):
    """Store with unmapped entity triggers LLM classification, cached for reuse."""
    from unittest.mock import MagicMock
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType
    from superlocalmemory.storage import schema

    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    mock_llm.response = "backend"

    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = {"Celery": "celery_01"}

    fact = AtomicFact(
        fact_id="f_llm1",
        content="Celery is our task queue",
        fact_type=FactType.SEMANTIC,
        entities=["Celery"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery is our task queue",
        session_id="s1",
    )

    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=entity_resolver, temporal_parser=None,
        db=db, llm=mock_llm,
    )
    assert result.domain_tags == ["backend"]
    assert mock_llm.call_count == 1

    # Second call with same entity — cached, no LLM call
    mock_llm.call_count = 0
    fact2 = AtomicFact(
        fact_id="f_llm2",
        content="Celery workers configured",
        fact_type=FactType.SEMANTIC,
        entities=["Celery"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    result2 = enrich_fact(
        fact2, record, "test",
        embedder=None, entity_resolver=entity_resolver, temporal_parser=None,
        db=db, llm=mock_llm,
    )
    assert result2.domain_tags == ["backend"]
    assert mock_llm.call_count == 0


def test_enrich_fact_llm_partial_match(tmp_path, mock_llm):
    """Partial match: Redis (seed) + Celery (unmapped). Only Celery triggers LLM."""
    from unittest.mock import MagicMock
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType
    from superlocalmemory.storage import schema

    db = DatabaseManager(tmp_path / "test2.db")
    db.initialize(schema)
    db.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('Redis', 'backend')")

    mock_llm.response = "devops"

    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = {"Celery": "celery_01", "Redis": "redis_01"}

    fact = AtomicFact(
        fact_id="f_llm3",
        content="Celery uses Redis as broker",
        fact_type=FactType.SEMANTIC,
        entities=["Celery", "Redis"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery uses Redis as broker",
        session_id="s1",
    )

    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=entity_resolver, temporal_parser=None,
        db=db, llm=mock_llm,
    )
    assert "backend" in result.domain_tags  # from Redis seed
    assert "devops" in result.domain_tags   # from LLM for Celery
    assert mock_llm.call_count == 1         # only Celery triggered LLM


def test_enrich_fact_no_llm_no_classification(tmp_path, mock_llm):
    """llm=None -> no LLM calls, domain_tags from rules only."""
    from unittest.mock import MagicMock
    from superlocalmemory.core.store_pipeline import enrich_fact
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType
    from superlocalmemory.storage import schema

    db = DatabaseManager(tmp_path / "test3.db")
    db.initialize(schema)
    db.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('Redis', 'backend')")

    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = {"Celery": "celery_01", "Redis": "redis_01"}

    fact = AtomicFact(
        fact_id="f_llm4",
        content="Celery uses Redis",
        fact_type=FactType.SEMANTIC,
        entities=["Celery", "Redis"],
        session_id="s1",
        confidence=0.9,
        importance=0.5,
    )
    record = MemoryRecord(
        profile_id="test",
        content="Celery uses Redis",
        session_id="s1",
    )

    result = enrich_fact(
        fact, record, "test",
        embedder=None, entity_resolver=entity_resolver, temporal_parser=None,
        db=db, llm=None,
    )
    assert result.domain_tags == ["backend"]  # only Redis seed
    assert mock_llm.call_count == 0           # LLM never called
