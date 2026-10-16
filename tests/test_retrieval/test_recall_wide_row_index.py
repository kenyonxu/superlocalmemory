"""A narrow covering join must not traverse stored vector overflow pages."""

from benchmark.recall_channels import seed

from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.fact_cover import fact_table
from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage.database import DatabaseManager


def test_wide_row_covering_index_keeps_ranked_hits(tmp_path):
    from superlocalmemory.storage.migrations import M058_recall_fact_cover as migration

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100, vectors=True, migrated=True)
    db.execute("DROP INDEX IF EXISTS idx_recall_fact_cover")
    before = (BM25Channel(db).search("Alice memory", "default"),
              TemporalChannel(db).search("Alice on 2025-03-15T00:00:00", "default"))
    with db.transaction():
        migration.apply(db._txn_state.conn)
        assert migration.verify(db._txn_state.conn)
    assert (BM25Channel(db).search("Alice memory", "default"),
            TemporalChannel(db).search("Alice on 2025-03-15T00:00:00", "default")) == before
    plan = db.execute(
        f"EXPLAIN QUERY PLAN SELECT created_at FROM {fact_table(db)} WHERE fact_id=? "
        "AND profile_id=? AND COALESCE(archive_status,'live')!='archived' "
        "AND COALESCE(quarantined,0)=0", ("f000000", "default"),
    )
    assert "COVERING INDEX idx_recall_fact_cover" in " ".join(str(tuple(r)) for r in plan)
    db.close()
