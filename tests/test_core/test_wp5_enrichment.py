"""Source preservation, historical dates and avoidable enrichment calls."""

import uuid
from unittest.mock import Mock, patch

import pytest

from superlocalmemory.core.engine_ingestion import build_engine_ingestion_command
from superlocalmemory.core.ingestion_command import IngestionRequest, IngestionState
from superlocalmemory.core.store_pipeline import enrich_fact
from superlocalmemory.encoding.temporal_parser import TemporalParser
from superlocalmemory.storage.models import (
    AtomicFact,
    ConsolidationAction,
    ConsolidationActionType,
    MemoryRecord,
)


def save(command, engine, content, **kw):
    return command.submit(
        IngestionRequest(
            content=content,
            profile_id=engine._profile_id,
            source_type="test",
            trusted_actor_id="test:owned",
            idempotency_key=uuid.uuid4().hex,
            **kw,
        )
    )


def test_same_save_consolidation_keeps_verbatim(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    source = "Priya renewed her licence on 2 October 2026 and sent the signed copy."
    receipt = save(command, engine, source)
    original = receipt.queryable_fact_ids[0]
    derivative = AtomicFact(content="Priya renewed her licence on 2 October 2026.")
    target = None

    def consolidate(fact, profile_id, **kw):
        nonlocal target
        if fact.fact_id == original:
            return ConsolidationAction(
                action_type=ConsolidationActionType.NOOP, existing_fact_id=target
            )
        target = engine._db.store_fact(fact)
        return ConsolidationAction(action_type=ConsolidationActionType.ADD, new_fact_id=target)

    with (
        patch.object(engine._fact_extractor, "extract_facts", return_value=[derivative]),
        patch.object(engine._consolidator, "consolidate", side_effect=consolidate),
    ):
        done = command.materialize(receipt.operation_id)
    assert done.state is IngestionState.COMPLETE
    assert engine._db.get_fact(original).content == source
    assert original in done.final_fact_ids
    assert engine._db.search_facts_fts("signed", engine._profile_id)


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("yesterday", "2020-01-10"),
        ("last Tuesday", "2020-01-07"),
    ],
)
def test_enrichment_uses_memory_date(relative, expected):
    record = MemoryRecord(session_date="2020-01-11", content=f"I met Priya {relative}.")
    fact = AtomicFact(content=record.content)
    enriched = enrich_fact(
        fact,
        record,
        "default",
        embedder=None,
        entity_resolver=None,
        temporal_parser=TemporalParser("2026-10-11"),
    )
    assert enriched.referenced_date[:10] == expected


def test_materialization_does_not_reclassify_each_model_fact(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    receipt = save(command, engine, "I use Python for payroll and store the records in SQLite.")
    router = Mock(wraps=engine._type_router)
    engine._type_router = router
    done = command.materialize(receipt.operation_id)
    assert done.state is IngestionState.COMPLETE
    router.route_facts.assert_not_called()


def test_long_source_embedding_calls_are_chunked(engine_with_mock_deps):
    engine = engine_with_mock_deps
    engine._embedder.embed.reset_mock()
    command = build_engine_ingestion_command(engine)
    receipt = save(command, engine, ("The harbor relay uses the backup channel. " * 900))
    with patch.object(engine._fact_extractor, "extract_facts", return_value=[]):
        done = command.materialize(receipt.operation_id)
    assert done.state is IngestionState.COMPLETE
    assert engine._embedder.embed.call_count
    assert all(len(c.args[0]) <= 4000 for c in engine._embedder.embed.call_args_list)


def test_warm_guard_uses_bounded_requests(engine_with_mock_deps):
    engine = engine_with_mock_deps
    engine._embedder._available = True
    engine._embedder._config = None
    engine._embedder.embed.reset_mock()
    engine.store_fast("The relay uses the backup channel. " * 800)
    assert engine._embedder.embed.call_count
    assert all(len(c.args[0]) <= 4000 for c in engine._embedder.embed.call_args_list)


def test_enrichment_preserves_real_retrieval_access_count():
    record = MemoryRecord(content="The relay uses channel four.")
    fact = AtomicFact(content=record.content, access_count=7)
    enriched = enrich_fact(
        fact, record, "default", embedder=None, entity_resolver=None, temporal_parser=None
    )
    assert enriched.access_count == 7


def test_assistant_first_person_is_not_attributed_to_user(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    receipt = save(command, engine, "I use Rust for payroll reconciliation.", role="assistant")
    done = command.materialize(receipt.operation_id)
    assert done.state is IngestionState.COMPLETE
    facts = engine._db.get_facts_by_ids(list(done.fact_ids), engine._profile_id)
    assert not any("the user" in f.content.lower() for f in facts)
    assert any("assistant" in f.content.lower() for f in facts)


def test_one_written_date_does_not_get_a_wall_clock_interval():
    source = "Priya renewed her licence on 2 October 2026."
    record = MemoryRecord(content=source, session_date="2026-03-14")
    fact = AtomicFact(content=source)
    enriched = enrich_fact(
        fact,
        record,
        "default",
        embedder=None,
        entity_resolver=None,
        temporal_parser=TemporalParser("2026-10-11"),
    )
    assert enriched.referenced_date[:10] == "2026-10-02"
    assert enriched.interval_end is None or enriched.interval_end[:10] == "2026-10-02"
