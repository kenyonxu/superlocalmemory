"""An FTS-backed writer must not retain an unread Python copy of the corpus."""

from benchmark.recall_channels import seed

from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.storage.database import DatabaseManager


def test_fts_writes_keep_legacy_tokens_without_retaining_python_documents(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 100)
    channel = BM25Channel(db)
    before = channel.search("Alice memory", "default")
    for i in range(100):
        profile = "default" if i % 10 < 8 else ("team" if i % 10 == 8 else "other")
        channel.add(f"f{i:06}", "Alice project memory release", profile)
    assert channel.search("Alice memory", "default") == before
    assert channel.document_count == 0
    assert len(db.get_all_bm25_tokens("default")) == 80
    db.execute("DROP TABLE atomic_facts_fts")
    assert channel.search("Alice memory", "default"), "fallback must cold-load persisted tokens"
    assert channel.document_count == 80
    db.close()


def test_active_fallback_is_not_cleared_by_a_concurrent_fts_writer(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    channel = BM25Channel(db)

    def fts_failure(*args, **kwargs):
        raise RuntimeError("FTS temporarily unavailable")

    monkeypatch.setattr(channel, "_fts5_search", fts_failure)
    before = channel.search("Alice memory", "default")
    started, release, writer_done = Event(), Event(), Event()
    get_scores = channel._bm25.get_scores

    def paused(tokens):
        started.set()
        assert release.wait(3)
        return get_scores(tokens)

    monkeypatch.setattr(channel._bm25, "get_scores", paused)

    def write():
        channel.add("f000000", "Alice memory", "default")
        writer_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(channel.search, "Alice memory", "default")
        assert started.wait(3)
        writer = pool.submit(write)
        try:
            assert writer_done.wait(3), "writer must not block an active legacy reader"
        finally:
            release.set()
        assert reader.result(timeout=3) == before
        writer.result(timeout=3)
    db.close()


def test_first_fallback_keeps_a_write_after_its_token_snapshot(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from superlocalmemory.storage.models import AtomicFact

    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20)
    for i in range(8):
        db.store_bm25_tokens(f"f{i:06}", "default", ["alice", "memory"])
    channel = BM25Channel(db)
    started, release = Event(), Event()
    get_tokens = db.get_all_bm25_tokens

    def paused(*args, **kwargs):
        snapshot = get_tokens(*args, **kwargs)
        started.set()
        assert release.wait(3)
        return snapshot

    def fts_failure(*args, **kwargs):
        raise RuntimeError("FTS temporarily unavailable")

    monkeypatch.setattr(db, "get_all_bm25_tokens", paused)
    monkeypatch.setattr(channel, "_fts5_search", fts_failure)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(channel.search, "uniquetoken", "default")
        assert started.wait(3)
        try:
            db.store_fact(AtomicFact(fact_id="fresh", memory_id="default", content="uniquetoken"))
            channel.add("fresh", "uniquetoken", "default")
        finally:
            release.set()
        assert "fresh" in dict(reader.result(timeout=3))
    assert "fresh" in dict(channel.search("uniquetoken", "default"))
    db.close()
