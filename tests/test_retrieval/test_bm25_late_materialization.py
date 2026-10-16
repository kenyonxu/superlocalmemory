"""FTS must rank narrow candidates before loading wide fact content."""

from benchmark.recall_channels import seed

from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.storage.database import DatabaseManager


def test_fts_materializes_content_only_after_the_existing_rank_limit(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100)
    queries = []
    execute = db.execute

    def trace(sql, params=()):
        if "bm25(atomic_facts_fts)" in sql:
            queries.append(sql)
        return execute(sql, params)

    db.execute = trace
    assert BM25Channel(db).search("Alice memory", "default")
    assert "AS MATERIALIZED" in queries[0]
    assert "af.content" not in queries[0].split(") SELECT")[0], "content must follow ranked LIMIT"
    db.close()


def test_custom_fts_rank_mapping_keeps_original_bm25_scores(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100)
    channel = BM25Channel(db)
    before = channel.search("Alice memory", "default")
    db.execute("INSERT INTO atomic_facts_fts(atomic_facts_fts,rank) "
               "VALUES ('rank','bm25(1.0,3.0)')")
    assert channel.search("Alice memory", "default") == before
    db.close()
