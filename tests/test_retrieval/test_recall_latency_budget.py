"""Default gate budget for the same wide-row workload profiled at 30k facts.

The gate uses 5k facts / 2.5k entities / 55k edges and the three owned channels.
No model loads. A cold build is warmed separately; every measured read follows
an access-accounting write, so the temporal cache must not invalidate on reads.
"""

import math
import time

import pytest
from benchmark.recall_channels import seed

from superlocalmemory.core.config import RetrievalConfig
from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.engine import RetrievalEngine
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage.database import DatabaseManager


@pytest.mark.latency_budget
def test_wide_store_recall_stays_within_latency_budget(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 5000, vectors=True, migrated=True)
    engine = RetrievalEngine(
        db, RetrievalConfig(use_cross_encoder=False, sufficiency_judge="off"),
        {"bm25": BM25Channel(db), "temporal": TemporalChannel(db),
         "entity_graph": EntityGraphChannel(db)},
    )
    query = "Alice on 2025-03-15T00:00:00 memory release"
    try:
        expected = [r.fact.fact_id for r in engine.recall(query, "default").results]
        times = []
        for sample in range(25):
            db.update_fact("f000000", {"access_count": sample})
            start = time.perf_counter()
            result = engine.recall(query, "default")
            times.append((time.perf_counter() - start) * 1000)
            assert [r.fact.fact_id for r in result.results] == expected
            assert not result.incomplete_channels
        p95 = sorted(times)[math.ceil(len(times) * .95) - 1]
        print(f"synthetic 5k wide store: p95={p95:.1f} ms, budget=150 ms")
        assert p95 < 150, f"wide synthetic recall p95={p95:.1f} ms; budget=150 ms"
    finally:
        engine.close(wait=True)
        db.close()
