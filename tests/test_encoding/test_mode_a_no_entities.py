"""E-H3: semantic consolidation works when entity extraction finds none."""

from unittest.mock import MagicMock

import pytest

from superlocalmemory.encoding.consolidator import MemoryConsolidator
from superlocalmemory.storage.models import AtomicFact, ConsolidationActionType, MemoryRecord
from tests.test_encoding.test_consolidator import _store_fact
from tests.test_encoding.test_consolidator import db as _fixture_db

db = _fixture_db


@pytest.mark.parametrize("negated", [False, True])
def test_entity_free_semantic_match_reaches_decision(db, negated):
    _store_fact(db, "old", "Always run tests.", embedding=[1.0, 0.0, 0.0])
    db.store_memory(MemoryRecord(memory_id="new-memory", content="parent"))
    fact = AtomicFact(
        fact_id="new",
        memory_id="new-memory",
        content="Never run tests." if negated else "Always run tests.",
        embedding=[1.0, 0.0, 0.0],
    )
    consolidator = MemoryConsolidator(db, embedder=MagicMock())
    action = consolidator.consolidate(fact, "default")
    assert action.action_type == (
        ConsolidationActionType.SUPERSEDE if negated else ConsolidationActionType.NOOP
    )
