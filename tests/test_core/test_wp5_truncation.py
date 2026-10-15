"""A searchable excerpt identifies its full source, which is always retained."""

import uuid

from superlocalmemory.core.engine_ingestion import build_engine_ingestion_command
from superlocalmemory.core.ingestion_command import IngestionRequest


def test_long_note_has_full_text_pointer_and_is_never_withheld(engine_with_mock_deps):
    engine = engine_with_mock_deps
    command = build_engine_ingestion_command(engine)
    source = "The harbor relay uses channel nine. " * 1600
    receipt = command.submit(
        IngestionRequest(
            content=source,
            profile_id=engine._profile_id,
            source_type="test",
            trusted_actor_id="test:owned",
            idempotency_key=uuid.uuid4().hex,
        )
    )
    fact = engine._db.get_fact(receipt.fact_ids[0])
    assert "truncated" in fact.content
    assert fact.memory_id in fact.content
    row = engine._db.execute("SELECT content FROM memories WHERE memory_id=?", (fact.memory_id,))[0]
    assert row[0] == source
    done = command.materialize(receipt.operation_id)
    assert done.state.value == "complete"
    assert engine._db.get_fact(fact.fact_id) is not None
