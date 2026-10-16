"""PR-243 finding 2: engine scope wiring belongs to WP-6.

Both prepare and search must receive include_global/include_shared. These
regressions use normal recall and real SQLite stores, with no reader pictures.
"""

import pytest

from superlocalmemory.core.config import RetrievalConfig
from superlocalmemory.retrieval.engine import RetrievalEngine
from superlocalmemory.retrieval.media_channel import MediaChannel
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord, Mode

from ._media_support import FakeClient, make_store, mid, unit


@pytest.fixture
def visual_recall(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    store = make_store(tmp_path, [(mid(1), "image", "picture", unit(0))])
    db.store_memory(MemoryRecord(
        memory_id="picture", content="[Image without text]",
        metadata={"_slm_source": {"type": "media", "media_id": mid(1)}},
    ))
    db.store_fact(AtomicFact(
        fact_id="picture-fact", memory_id="picture", content="[Image without text]",
    ))
    engine = RetrievalEngine(db, RetrievalConfig(use_cross_encoder=False), channels={})
    engine._media_channel = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db)
    try:
        assert store.vector_count("reader") == 0
        yield db, engine
    finally:
        engine.close()
        store.close()
        db.close()


@pytest.mark.parametrize("scope,flags", [
    ("global", {"include_global": True, "include_shared": False}),
    ("shared", {"include_global": False, "include_shared": True}),
])
@pytest.mark.parametrize("gate", ["prepare", "search"])
def test_normal_recall_finds_visible_wordless_picture(
    visual_recall, monkeypatch, scope, flags, gate,
):
    db, engine = visual_recall
    db.execute("UPDATE atomic_facts SET scope=?, shared_with='[\"reader\"]'", (scope,))
    if gate == "search":
        # Isolate the second missing flag: supply a ready query vector so this
        # case fails even after WP-6 has fixed prepare but before fixing search.
        monkeypatch.setattr(engine._media_channel, "prepare", lambda *a, **kw: (unit(0), None))
    response = engine.recall("a photo of a harbour", "reader", Mode.A, **flags)
    assert [r.fact.fact_id for r in response.results] == ["picture-fact"]
    assert response.results[0].channel_scores["media"] == pytest.approx(1.0)


@pytest.mark.parametrize("scope,audience,flags", [
    ("global", "[]", {"include_global": False, "include_shared": False}),
    ("shared", '["somebody-else"]', {"include_global": True, "include_shared": True}),
    ("personal", "[]", {"include_global": True, "include_shared": True}),
])
def test_normal_recall_excludes_unauthorized_picture(visual_recall, scope, audience, flags):
    db, engine = visual_recall
    db.execute("UPDATE atomic_facts SET scope=?, shared_with=?", (scope, audience))
    assert engine.recall("a photo of a harbour", "reader", Mode.A, **flags).results == []
