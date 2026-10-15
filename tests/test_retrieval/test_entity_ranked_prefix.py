"""A top-k graph page authorizes a prefix, backfilling every rejected rank."""

from unittest.mock import patch

import pytest
from benchmark.recall_channels import seed

from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.retrieval.scope_policy import filter_authorized_results
from superlocalmemory.storage.database import DatabaseManager


def test_search_authorizes_only_the_ranked_page(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 200)
    channel = EntityGraphChannel(db)
    sizes = []

    def counted(*args, **kwargs):
        sizes.append(len(args[1]))
        return filter_authorized_results(*args, **kwargs)

    with patch("superlocalmemory.retrieval.entity_channel.filter_authorized_results", counted):
        results = channel.search("Alice", "default", top_k=10)
    assert len(results) == 10
    assert max(sizes) <= 10, "authorization must not hydrate/scan every activated rank"
    db.close()


@pytest.mark.parametrize("top_k", [4, 10, 50])
def test_sparse_visibility_has_a_bounded_authorization_query_count(tmp_path, top_k):
    from superlocalmemory.retrieval.entity_channel import ranked_authorized_topk

    db = DatabaseManager(tmp_path / "sparse.db")
    try:
        seed(db, 20)
        with db.transaction():
            db._txn_state.conn.executemany(
                "INSERT INTO atomic_facts(fact_id,memory_id,profile_id,content) "
                "VALUES (?,'team',?,'synthetic sparse rank')",
                ((f"s{i}", "default" if i in (0, 10000, 19999) else "team")
                 for i in range(20000)),
            )
        ranked = [(f"s{i}", 1 - i / 20000) for i in range(20000)]
        expected = filter_authorized_results(db, ranked, "default")[:top_k]
        with patch.object(db, "visible_fact_ids", wraps=db.visible_fact_ids) as authorize:
            assert ranked_authorized_topk(db, ranked, "default", top_k) == expected
            assert authorize.call_count <= 1 + (20000 + 63) // 64
        assert len(expected) == 3
    finally:
        db.close()


def test_prefix_backfill_preserves_full_filter_ranking(tmp_path):
    from superlocalmemory.retrieval.entity_channel import ranked_authorized_topk

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 200)
    ranked = [(f"f{i:06}", 1 - i / 200) for i in range(200)]
    db.execute("UPDATE atomic_facts SET quarantined=1 WHERE fact_id IN ('f000000','f000001')")
    for profile in ("default", "team", "other"):
        for global_scope, shared_scope in ((False, False), (True, False), (True, True)):
            flags = {"include_global": global_scope, "include_shared": shared_scope}
            expected = filter_authorized_results(db, ranked, profile, **flags)[:20]
            assert ranked_authorized_topk(db, ranked, profile, 20, **flags) == expected
    db.close()
