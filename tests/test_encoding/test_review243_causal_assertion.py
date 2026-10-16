"""PR-243: scattered cause words must not create a persisted causal edge."""

import pytest

from superlocalmemory.encoding.graph_builder import GraphBuilder
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, EdgeType, MemoryRecord


@pytest.mark.parametrize("prior_text,expected", [
    ("Atlas vendor audit passed after its failed rehearsal", False),
    ("Atlas vendor audit passed; the rehearsal failed", False),
    ("Atlas vendor audit passed and its rehearsal failed", False),
    ("Atlas vendor audit passed. Failed rehearsal was repeated", False),
    ("Atlas failed its vendor audit rehearsal", False),
    ("Atlas vendor audit failed", True),
    ("Atlas vendor audit failed after its successful rehearsal", True),
])
def test_graph_persists_only_an_asserted_cause_endpoint(tmp_path, prior_text, expected):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    try:
        observations = [
            ("prior", prior_text, "2026-01-01"),
            ("effect", "Atlas rollout was delayed because the vendor audit failed", "2026-01-02"),
        ]
        for fid, content, date in observations:
            db.store_memory(MemoryRecord(memory_id=fid, content=content))
            db.store_fact(AtomicFact(
                fact_id=fid, memory_id=fid, content=content,
                canonical_entities=["atlas"], observation_date=date, created_at=date,
            ))
        effect = db.get_fact("effect", "default")
        edges = GraphBuilder(db).build_edges(effect, "default")
        causal = [(e.source_id, e.target_id) for e in edges if e.edge_type == EdgeType.CAUSAL]
        assert causal == ([("prior", "effect")] if expected else [])
        persisted = db.execute(
            "SELECT source_id, target_id FROM graph_edges WHERE edge_type='causal'",
        )
        assert [(r["source_id"], r["target_id"]) for r in persisted] == causal
    finally:
        db.close()
