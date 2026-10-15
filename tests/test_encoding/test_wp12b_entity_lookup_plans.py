"""Encoding entity selectors must use M028's association index."""

from unittest.mock import patch

import pytest

from superlocalmemory.encoding.entity_resolver import EntityResolver
from superlocalmemory.encoding.temporal_validator import TemporalValidator
from superlocalmemory.storage import entity_index, schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, CanonicalEntity, MemoryRecord


@pytest.fixture
def seeded(tmp_path):
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    memory = MemoryRecord(content="Entity lookup fixture.")
    db.store_memory(memory)
    for eid in ("keep", "merge", "premergeSuffix"):
        db.store_entity(CanonicalEntity(entity_id=eid, canonical_name=eid))
    for fid, entities in (("match", ["merge", "keep"]),
                          ("substring", ["premergeSuffix"]), ("self", ["merge"])):
        db.store_fact(AtomicFact(fact_id=fid, memory_id=memory.memory_id,
                                content=f"Fact {fid}.", canonical_entities=entities))
    entity_index.backfill(db.db_path, max_batches=10)
    assert entity_index.is_complete(db)
    yield db
    db.close()


def _assert_indexed_selects(db, selects):
    assert selects, "The production call must execute an entity/fact selector"
    for sql, params in selects:
        plan = db.execute("EXPLAIN QUERY PLAN " + sql, params)
        details = " ".join(str(dict(row)["detail"]) for row in plan)
        assert "idx_fact_entity_associations_entity" in details, details
        assert "SCAN af" not in details, details


def _capture(db, selects):
    execute = db.execute

    def run(sql, params=()):
        if sql.lstrip().startswith("SELECT") and "canonical_entities_json" in sql:
            selects.append((sql, params))
        return execute(sql, params)

    return run


def test_temporal_candidates_use_index_and_exact_membership(seeded):
    selects = []
    validator = TemporalValidator(seeded)
    new = AtomicFact(fact_id="self", canonical_entities=["merge"])
    with patch.object(seeded, "execute", side_effect=_capture(seeded, selects)):
        candidates = validator._entity_based_candidates(new, "default")
    assert candidates == ["match"]
    _assert_indexed_selects(seeded, selects)


def test_merge_uses_index_and_rewrites_all_entity_memberships(seeded):
    selects = []
    with patch.object(seeded, "execute", side_effect=_capture(seeded, selects)):
        EntityResolver(seeded).merge_entities("keep", "merge", "default")
    _assert_indexed_selects(seeded, selects)
    for fid in ("match", "self"):
        assert seeded.get_fact(fid, "default").canonical_entities == ["keep"]
    assert seeded.get_fact("substring", "default").canonical_entities == ["premergeSuffix"]
    indexed = seeded.execute("SELECT fact_id FROM fact_entity_associations WHERE entity_id='keep'")
    assert {dict(row)["fact_id"] for row in indexed} == {"match", "self"}


def test_merge_before_backfill_preserves_legacy_facts(seeded):
    seeded.execute("DELETE FROM fact_entity_associations")
    seeded.execute("DELETE FROM fact_entity_association_repair_state")
    EntityResolver(seeded).merge_entities("keep", "merge", "default")
    assert seeded.get_fact("match", "default").canonical_entities == ["keep"]
