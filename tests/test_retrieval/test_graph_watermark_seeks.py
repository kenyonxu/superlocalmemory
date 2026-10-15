"""Reading both extrema together must not scan the retained fact change log."""

from benchmark.recall_channels import seed

from superlocalmemory.retrieval import graph_catchup
from superlocalmemory.storage.database import DatabaseManager


def test_graph_watermark_uses_extrema_seeks(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100)
    plans = []
    execute = db.execute

    def trace(sql, params=()):
        if "fact_search_changes" in sql:
            plans.extend(str(tuple(row)) for row in execute("EXPLAIN QUERY PLAN " + sql, params))
        return execute(sql, params)

    db.execute = trace
    assert graph_catchup.read_watermark(db) == (100, 1100)
    assert not any("SCAN fact_search_changes" in plan for plan in plans), plans
    db.execute("DELETE FROM fact_search_changes WHERE seq < 50")
    assert graph_catchup.read_watermark(db) == (100, 1100)
    db.execute("DELETE FROM fact_search_changes")
    assert graph_catchup.read_watermark(db) == (0, 1100)
    db.close()
