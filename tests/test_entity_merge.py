"""Tests for entity merge functionality."""

import json
import pytest
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import CanonicalEntity, EntityAlias, _new_id, _now


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(str(tmp_path / "test.db"))
    d.initialize(schema)
    return d


def _make_entity(db, name, profile_id="default", scope="personal", entity_type="technology"):
    entity = CanonicalEntity(
        entity_id=_new_id(), profile_id=profile_id, scope=scope,
        canonical_name=name, entity_type=entity_type,
        first_seen=_now(), last_seen=_now(), fact_count=3,
    )
    db.store_entity(entity)
    db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=entity.entity_id,
        alias=name, confidence=1.0, source="canonical",
    ))
    return entity


def test_get_entities_by_scope(db):
    _make_entity(db, "React", scope="personal")
    _make_entity(db, "Docker", scope="global")
    _make_entity(db, "Python", scope="personal")
    results = db.get_entities_by_scope("default", scope="personal")
    assert len(results) == 2
    names = {e.canonical_name for e in results}
    assert names == {"React", "Python"}


def test_merge_entities_basic(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    db.store_alias(EntityAlias(
        alias_id=_new_id(), entity_id=source.entity_id,
        alias="React.js", confidence=0.9, source="fuzzy",
    ))
    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )
    assert result["aliases_moved"] == 2  # "ReactJS" + "React.js"
    assert result["source_deleted"] is True
    aliases = db.get_aliases_for_entity(target.entity_id)
    alias_texts = {a.alias for a in aliases}
    assert "ReactJS" in alias_texts
    assert "React.js" in alias_texts
    assert "React" in alias_texts


def test_merge_entities_updates_facts(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    memory_id = _new_id()
    db.execute(
        "INSERT INTO memories "
        "(memory_id, profile_id, content, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (memory_id, "default", "React uses JSX"),
    )
    fact_id = _new_id()
    db.execute(
        "INSERT INTO atomic_facts "
        "(fact_id, memory_id, profile_id, content, confidence, scope, "
        " entities_json, canonical_entities_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (fact_id, memory_id, "default", "React uses JSX", 0.9, "personal",
         json.dumps(["ReactJS"]), json.dumps([source.entity_id])),
    )
    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )
    assert result["facts_updated"] == 1
    rows = db.execute(
        "SELECT canonical_entities_json FROM atomic_facts WHERE fact_id = ?",
        (fact_id,),
    )
    entities = json.loads(dict(rows[0])["canonical_entities_json"])
    assert target.entity_id in entities
    assert source.entity_id not in entities


def test_merge_entities_updates_graph_edges(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    target = _make_entity(db, "React", scope="global")
    other = _make_entity(db, "TypeScript", scope="global")
    edge_id = _new_id()
    db.execute(
        "INSERT INTO graph_edges "
        "(edge_id, profile_id, source_id, target_id, edge_type, weight, "
        " created_at, scope) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
        (edge_id, "default", source.entity_id, other.entity_id,
         "entity", 0.8, "personal"),
    )
    result = db.merge_entities(
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        profile_id="default",
    )
    assert result["edges_updated"] == 1
    rows = db.execute(
        "SELECT source_id FROM graph_edges WHERE edge_id = ?",
        (edge_id,),
    )
    assert dict(rows[0])["source_id"] == target.entity_id


def test_merge_entities_same_id_raises(db):
    entity = _make_entity(db, "React", scope="global")
    with pytest.raises(ValueError, match="same entity"):
        db.merge_entities(
            source_entity_id=entity.entity_id,
            target_entity_id=entity.entity_id,
            profile_id="default",
        )


def test_merge_entities_target_not_found(db):
    source = _make_entity(db, "ReactJS", scope="personal")
    with pytest.raises(ValueError, match="Target entity"):
        db.merge_entities(
            source_entity_id=source.entity_id,
            target_entity_id="nonexistent_id",
            profile_id="default",
        )
