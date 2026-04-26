"""Phase 3 tests — global authoritative entities."""

from __future__ import annotations

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, EntityAlias, _new_id, _now
from superlocalmemory.encoding.entity_resolver import EntityResolver


def _ensure_profiles(db: DatabaseManager, *profile_ids: str) -> None:
    """Insert profile rows so FK constraints on canonical_entities succeed."""
    for pid in profile_ids:
        db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
            (pid, pid),
        )


@pytest.fixture
def db_with_global_entity(tmp_path):
    """DB with a global-scope 'React' entity already created."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    _ensure_profiles(db, "creator_agent", "other_agent", "agent_a", "agent_b")
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator_agent",
        scope="global",
        canonical_name="React",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))
    return db, entity_id


def test_global_entity_found_before_personal(db_with_global_entity):
    """Tier 0: resolve() finds global entity before checking personal scope."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    result = resolver.resolve(["React"], profile_id="other_agent")
    assert "React" in result
    assert result["React"] == global_id


def test_global_entity_shared_across_agents(db_with_global_entity):
    """Different agents resolve to the same global entity ID."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    r1 = resolver.resolve(["React"], profile_id="agent_a")
    r2 = resolver.resolve(["React"], profile_id="agent_b")
    assert r1["React"] == global_id
    assert r2["React"] == global_id


def test_no_global_entity_falls_back_to_personal(tmp_path):
    """No global entity -> falls back to existing personal entity (Tier a)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    _ensure_profiles(db, "agent_a")
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="agent_a",
        scope="personal",
        canonical_name="MySecretProject",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))

    resolver = EntityResolver(db)
    result = resolver.resolve(["MySecretProject"], profile_id="agent_a")
    assert result["MySecretProject"] == entity_id


def test_no_match_creates_global_entity(tmp_path):
    """No existing entity -> creates new one in global scope."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    _ensure_profiles(db, "agent_a")

    resolver = EntityResolver(db)
    result = resolver.resolve(["Celery"], profile_id="agent_a")
    assert "Celery" in result
    new_id = result["Celery"]

    rows = db.execute(
        "SELECT scope, canonical_name FROM canonical_entities WHERE entity_id = ?",
        (new_id,),
    )
    assert len(rows) == 1
    assert rows[0]["scope"] == "global"
    assert rows[0]["canonical_name"] == "Celery"


def test_global_lookup_case_insensitive(db_with_global_entity):
    """Global lookup is case-insensitive."""
    db, global_id = db_with_global_entity
    resolver = EntityResolver(db)

    result = resolver.resolve(["react", "REACT"], profile_id="agent_a")
    assert result["react"] == global_id
    assert result["REACT"] == global_id


def test_alias_lookup_finds_global_entity(tmp_path):
    """_alias_lookup finds global entity via alias (cross-scope)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    _ensure_profiles(db, "creator", "other_agent")
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator",
        scope="global",
        canonical_name="React",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))
    db.store_alias(EntityAlias(
        alias_id=_new_id(),
        entity_id=entity_id,
        alias="ReactJS",
        confidence=0.9,
        source="manual",
    ))

    resolver = EntityResolver(db)
    result = resolver.resolve(["ReactJS"], profile_id="other_agent")
    assert "ReactJS" in result
    assert result["ReactJS"] == entity_id


def test_fuzzy_match_finds_global_entity(tmp_path):
    """_fuzzy_match finds global entity (cross-scope fuzzy match)."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    _ensure_profiles(db, "creator", "other_agent")
    entity_id = _new_id()
    now = _now()
    db.store_entity(CanonicalEntity(
        entity_id=entity_id,
        profile_id="creator",
        scope="global",
        canonical_name="Kubernetes",
        entity_type="concept",
        first_seen=now,
        last_seen=now,
        fact_count=0,
    ))

    resolver = EntityResolver(db)
    result = resolver.resolve(["Kuberntes"], profile_id="other_agent")
    assert "Kuberntes" in result
    assert result["Kuberntes"] == entity_id
