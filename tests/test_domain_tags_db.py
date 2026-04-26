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
    dbm.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('React', 'frontend')")
    dbm.execute(
        "INSERT INTO domain_mapping (entity_name, domain) VALUES ('TypeScript', 'frontend')"
    )
    dbm.execute("INSERT INTO domain_mapping (entity_name, domain) VALUES ('PostgreSQL', 'backend')")
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
        "alice",
        "personal",
        False,
        True,
        "",
        skill_tags=["backend", "devops"],
    )
    assert "domain_tags IS NOT NULL" in clause
    assert "json_each" in clause
    assert "backend" in params
    assert "devops" in params


def test_scope_where_without_skill_tags():
    clause, params = DatabaseManager._scope_where(
        "alice",
        "personal",
        False,
        True,
        "",
    )
    assert "domain_tags" not in clause


def test_domain_recall_visibility(in_memory_db):
    """Agent with matching skill_tags sees domain-tagged facts."""
    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')")
    in_memory_db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')")
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
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["frontend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}",
        params,
    )
    contents = [r["content"] for r in rows]
    assert "react tip" in contents

    clause2, params2 = DatabaseManager._scope_where(
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["backend"],
    )
    rows2 = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause2}",
        params2,
    )
    contents2 = [r["content"] for r in rows2]
    assert "react tip" not in contents2


def test_slm_config_skill_tags():
    from superlocalmemory.core.config import SLMConfig

    config = SLMConfig(skill_tags=["backend", "devops"])
    assert config.skill_tags == ["backend", "devops"]


def test_slm_config_skill_tags_default():
    from superlocalmemory.core.config import SLMConfig

    config = SLMConfig()
    assert config.skill_tags == []


def test_profile_skill_tags():
    from superlocalmemory.storage.models import Profile

    p = Profile(profile_id="x", name="X", config={"skill_tags": ["frontend"]})
    assert p.skill_tags == ["frontend"]


def test_profile_skill_tags_default():
    from superlocalmemory.storage.models import Profile

    p = Profile(profile_id="x", name="X")
    assert p.skill_tags == []


def test_enrich_fact_resolves_domain_tags(dbm_with_mappings):
    """enrich_fact resolves domain_tags from entity names."""
    from unittest.mock import MagicMock
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord, FactType
    from superlocalmemory.core.store_pipeline import enrich_fact

    entity_resolver = MagicMock()
    entity_resolver.resolve.return_value = {"React": "react_01"}

    fact = AtomicFact(
        content="React uses JSX",
        entities=["React"],
        fact_type=FactType.SEMANTIC,
    )
    record = MemoryRecord(memory_id="m1", profile_id="test")
    embedder = MagicMock()
    embedder.embed.return_value = None

    enriched = enrich_fact(
        fact,
        record,
        "test",
        embedder=embedder,
        entity_resolver=entity_resolver,
        temporal_parser=None,
        db=dbm_with_mappings,
    )
    assert enriched.domain_tags == ["frontend"]


def test_retrieval_engine_stores_skill_tags():
    """RetrievalEngine constructor stores skill_tags."""
    from unittest.mock import MagicMock
    from superlocalmemory.retrieval.engine import RetrievalEngine

    engine = RetrievalEngine(
        db=MagicMock(),
        config=MagicMock(),
        channels={},
        skill_tags=["backend"],
    )
    assert engine._skill_tags == ["backend"]


def test_domain_and_shared_with_coexist(in_memory_db):
    """Both shared_with and domain matching return results."""
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
        "b",
        "personal",
        False,
        True,
        "",
        skill_tags=["backend"],
    )
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {clause}",
        params,
    )
    contents = [r["content"] for r in rows]
    assert "shared fact" in contents
    assert "domain fact" in contents


def test_add_domain_mapping_tool():
    """add_domain_mapping MCP tool inserts mapping."""
    from unittest.mock import MagicMock
    from superlocalmemory.mcp.tools_core import register_core_tools

    server = MagicMock()
    tools = {}

    def capture_tool(annotations=None):
        def decorator(fn):
            tools[fn.__name__] = fn
            return fn

        return decorator

    server.tool = capture_tool

    mock_engine = MagicMock()
    mock_engine.profile_id = "test"
    register_core_tools(server, lambda: mock_engine)

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )

    assert result["success"] is True
    assert "SolidJS" in result["mapping"]["entity_name"]


def test_add_domain_mapping_duplicate():
    """Adding duplicate is idempotent."""
    from unittest.mock import MagicMock
    from superlocalmemory.mcp.tools_core import register_core_tools

    server = MagicMock()
    tools = {}
    server.tool = lambda annotations=None: lambda fn: (tools.update({fn.__name__: fn}), fn)[1]

    mock_engine = MagicMock()
    mock_engine.profile_id = "test"
    register_core_tools(server, lambda: mock_engine)

    import asyncio

    asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )
    result = asyncio.get_event_loop().run_until_complete(
        tools["add_domain_mapping"](entity_name="SolidJS", domain="frontend")
    )

    assert result["success"] is True


# ------------------------------------------------------------------
# Phase 2B: KNOWN_DOMAINS + get_unmapped_entities
# ------------------------------------------------------------------


def test_known_domains_constant():
    """KNOWN_DOMAINS contains the 5 expected domains."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    assert set(KNOWN_DOMAINS) == {"frontend", "backend", "devops", "mobile", "data"}


def test_get_unmapped_entities_partial(dbm_with_mappings):
    """Returns only entities with no domain_mapping row."""
    unmapped = dbm_with_mappings.get_unmapped_entities(["React", "Celery", "Prisma"])
    assert unmapped == ["Celery", "Prisma"]


def test_get_unmapped_entities_all_mapped(dbm_with_mappings):
    """All entities mapped returns empty list."""
    unmapped = dbm_with_mappings.get_unmapped_entities(["React"])
    assert unmapped == []


def test_get_unmapped_entities_empty_input(dbm_with_mappings):
    """Empty input returns empty list."""
    unmapped = dbm_with_mappings.get_unmapped_entities([])
    assert unmapped == []


def test_remove_domain_mapping(dbm_with_mappings):
    """Delete a domain_mapping row."""
    # Verify row exists before delete
    before = dbm_with_mappings.execute(
        "SELECT * FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("React", "frontend"),
    )
    assert len(before) == 1

    dbm_with_mappings.execute(
        "DELETE FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("React", "frontend"),
    )

    # Verify row is gone
    after = dbm_with_mappings.execute(
        "SELECT * FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("React", "frontend"),
    )
    assert len(after) == 0


def test_remove_domain_mapping_nonexistent(dbm_with_mappings):
    """Deleting non-existent mapping is a no-op."""
    count_before = len(
        dbm_with_mappings.execute("SELECT * FROM domain_mapping")
    )

    dbm_with_mappings.execute(
        "DELETE FROM domain_mapping WHERE entity_name = ? AND domain = ?",
        ("NonExistent", "frontend"),
    )

    count_after = len(
        dbm_with_mappings.execute("SELECT * FROM domain_mapping")
    )
    assert count_after == count_before


# ------------------------------------------------------------------
# Phase 2B: classify_and_cache_domain
# ------------------------------------------------------------------


class MockLLMBackbone:
    """Minimal mock for LLMBackbone with controllable responses."""

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
    return MockLLMBackbone()


def test_classify_valid_domain(dbm_with_mappings, mock_llm):
    """LLM returns valid domain -> cached in domain_mapping, returned."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm.response = "backend"
    result = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result == "backend"
    # Verify cached in DB
    rows = dbm_with_mappings.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = 'Celery'"
    )
    assert len(rows) == 1
    assert rows[0]["domain"] == "backend"


def test_classify_unknown_response(dbm_with_mappings, mock_llm):
    """LLM returns 'unknown' -> not cached, returns None."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm.response = "unknown"
    result = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result is None
    rows = dbm_with_mappings.execute(
        "SELECT domain FROM domain_mapping WHERE entity_name = 'Celery'"
    )
    assert len(rows) == 0


def test_classify_garbage_response(dbm_with_mappings, mock_llm):
    """LLM returns garbage -> not cached, returns None."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm.response = "I think this is a backend tool"
    result = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result is None


def test_classify_llm_exception(dbm_with_mappings, mock_llm):
    """LLM raises exception -> returns None."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm.should_raise = True
    result = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result is None


def test_classify_idempotent_cached(dbm_with_mappings, mock_llm):
    """Already cached entity returns from cache without LLM call."""
    from superlocalmemory.storage.seed_domain_mapping import KNOWN_DOMAINS

    mock_llm.response = "backend"
    result1 = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result1 == "backend"
    assert mock_llm.call_count == 1

    mock_llm.call_count = 0
    result2 = dbm_with_mappings.classify_and_cache_domain("Celery", mock_llm, KNOWN_DOMAINS)
    assert result2 == "backend"
    assert mock_llm.call_count == 0  # LLM not called — early return from cache
