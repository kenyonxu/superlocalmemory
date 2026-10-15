"""Enrichment must use the existing complete entity index, with scan parity."""

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.entity_index import backfill
from superlocalmemory.storage.models import AtomicFact, CanonicalEntity, MemoryRecord


def test_complete_index_serves_entity_facts_without_a_profile_scan(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    db.store_memory(MemoryRecord(memory_id="m", profile_id="default", content="source"))
    db.store_entity(CanonicalEntity(entity_id="e", profile_id="default", canonical_name="Zephyr"))
    for i in range(10):
        db.store_fact(
            AtomicFact(
                fact_id=f"f{i}",
                memory_id="m",
                profile_id="default",
                content=f"Zephyr uses channel {i}.",
                canonical_entities=["e"],
            )
        )
    backfill(db.db_path, max_batches=100)
    statements = []
    original = db.execute

    def record(sql, params=()):
        statements.append(sql)
        return original(sql, params)

    db.execute = record
    facts = db.get_facts_by_entity("e", "default")
    assert len(facts) == 10
    lookups = [sql for sql in statements if "LIKE" in sql]
    assert lookups and all("fact_entity_associations" in sql for sql in lookups)
    db.close()


def test_source_identity_lookup_has_an_index(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    rows = db.execute(
        "EXPLAIN QUERY PLAN SELECT fact_id FROM atomic_facts "
        "WHERE profile_id=? AND content=? AND scope=? AND memory_id=?",
        ("default", "source", "personal", "m"),
    )
    assert any("idx_facts_source_identity" in str(tuple(row)) for row in rows)
    db.close()
