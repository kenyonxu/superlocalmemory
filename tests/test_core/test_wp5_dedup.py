"""Equal strings cannot erase visibility or event provenance."""

import json
import uuid

from superlocalmemory.core.engine_ingestion import build_engine_ingestion_command
from superlocalmemory.core.ingestion_command import IngestionRequest

TEXT = "The Zephyr certificate expires on 2027-01-11."


def submit(engine, command, **kw):
    return command.submit(
        IngestionRequest(
            content=TEXT,
            profile_id=engine._profile_id,
            source_type="test",
            trusted_actor_id="test:owned",
            idempotency_key=uuid.uuid4().hex,
            **kw,
        )
    )


def test_duplicate_reuses_parent_merges_metadata_without_access(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    a = submit(
        engine,
        command,
        metadata={"project": "zephyr", "tags": ["first"]},
        session_date="2026-01-11",
    )
    b = submit(
        engine,
        command,
        metadata={"project": "zephyr", "tags": ["second"], "note": "new"},
        session_date="2026-09-11",
    )
    assert a.queryable_fact_ids == b.queryable_fact_ids
    assert len(engine._db.execute("SELECT * FROM memories")) == 1
    fact = engine._db.get_fact(a.queryable_fact_ids[0])
    assert fact.access_count == 0
    metadata = json.loads(engine._db.execute("SELECT metadata_json FROM memories")[0][0])
    assert set(metadata["tags"]) >= {"first", "second"}
    assert metadata["note"] == "new"
    assert set(metadata["event_dates"]) == {"2026-01-11", "2026-09-11"}


def test_equal_content_preserves_scope_project_and_shared_audience(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    receipts = [
        submit(engine, command, metadata={"project": "old"}, session_date="2026-01-11"),
        submit(
            engine,
            command,
            scope="shared",
            shared_with=("default",),
            metadata={"project": "new"},
            session_date="2026-09-11",
        ),
        submit(
            engine,
            command,
            scope="global",
            metadata={"project": "global"},
            session_date="2026-10-11",
        ),
        submit(
            engine,
            command,
            scope="shared",
            shared_with=("other",),
            metadata={"project": "new"},
            session_date="2026-09-11",
        ),
    ]
    assert len({r.queryable_fact_ids[0] for r in receipts}) == 4
    for receipt in receipts:
        assert command.materialize(receipt.operation_id).state.value == "complete"


def test_legacy_fast_store_reuses_parent_without_losing_metadata(engine_with_mock_deps):
    engine = engine_with_mock_deps
    a = engine.store_fast(TEXT, metadata={"project": "zephyr", "tags": ["first"]})
    b = engine.store_fast(TEXT, metadata={"project": "zephyr", "tags": ["second"]})
    assert a == b
    assert len(engine._db.execute("SELECT * FROM memories")) == 1
    metadata = json.loads(engine._db.execute("SELECT metadata_json FROM memories")[0][0])
    assert set(metadata["tags"]) == {"first", "second"}
    assert engine._db.get_fact(a[0]).access_count == 0


def test_concurrent_fast_duplicates_share_one_source(engine_with_mock_deps):
    from concurrent.futures import ThreadPoolExecutor

    engine = engine_with_mock_deps
    with ThreadPoolExecutor(max_workers=4) as pool:
        writes = list(
            pool.map(
                lambda n: engine.store_fast(TEXT, metadata={"project": "zephyr", "tags": [str(n)]}),
                range(4),
            )
        )
    assert len({tuple(ids) for ids in writes}) == 1
    assert len(engine._db.execute("SELECT * FROM memories")) == 1
    metadata = json.loads(engine._db.execute("SELECT metadata_json FROM memories")[0][0])
    assert set(metadata["tags"]) == {"0", "1", "2", "3"}


def test_consolidation_does_not_dedup_across_scope_or_project(engine_with_mock_deps):
    from unittest.mock import patch

    from superlocalmemory.storage.models import AtomicFact, MemoryRecord

    engine = engine_with_mock_deps
    for mid, scope, project in [("old", "personal", "old"), ("new", "global", "new")]:
        engine._db.store_memory(
            MemoryRecord(
                memory_id=mid,
                profile_id=engine._profile_id,
                content=TEXT,
                scope=scope,
                metadata={"project": project},
            )
        )
    old = AtomicFact(
        memory_id="old", profile_id=engine._profile_id, content=TEXT, embedding=[0.1] * 768
    )
    engine._db.store_fact(old)
    new = AtomicFact(
        memory_id="new",
        profile_id=engine._profile_id,
        content=TEXT,
        scope="global",
        embedding=[0.1] * 768,
    )
    with patch.object(engine._consolidator, "_similar", return_value=[(old, 0.99)]):
        candidates = engine._consolidator._find_candidates(new, engine._profile_id)
    assert not candidates


def test_relative_date_is_recomputed_on_resave(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    import uuid

    source = "I renewed the Zephyr licence yesterday."
    for event, expected in [("2020-01-11", "2020-01-10"), ("2020-09-11", "2020-09-10")]:
        receipt = command.submit(
            IngestionRequest(
                content=source,
                profile_id=engine._profile_id,
                source_type="test",
                trusted_actor_id="test:owned",
                idempotency_key=uuid.uuid4().hex,
                session_date=event,
            )
        )
        assert command.materialize(receipt.operation_id).state.value == "complete"
        assert engine._db.get_fact(receipt.queryable_fact_ids[0]).referenced_date[:10] == expected
