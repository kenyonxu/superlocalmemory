"""The visible-memory set for shared and global picture search is worked out once per recall,
kept for the same 30 seconds as the private path, and dropped when a picture or a scope changes."""
import pytest

from superlocalmemory.media.generation import bump_generation
from superlocalmemory.retrieval import media_scope
from superlocalmemory.retrieval.media_channel import ACTIVE_TTL_S, MediaChannel
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

from ._media_support import FakeClient, make_store, mid, unit


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def channel(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    store = make_store(tmp_path, [(mid(1), "image", "picture", unit(0))])
    db.store_memory(MemoryRecord(memory_id="picture", profile_id="default", content="p",
                                 metadata={"_slm_source": {"type": "media", "media_id": mid(1)}}))
    db.store_fact(AtomicFact(fact_id="pf", memory_id="picture", content="p", profile_id="default",
                             scope="global"))
    calls = []
    real = media_scope.visible_memories
    monkeypatch.setattr(media_scope, "visible_memories",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    clock = Clock()
    ch = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db, clock=clock)
    try:
        yield ch, db, calls, clock
    finally:
        store.close()
        db.close()


def recall(ch, **flags):
    ch.prepare("q", "reader", **flags)
    return ch.search(unit(0), "reader", 10, **flags)


def test_one_recall_resolves_the_visible_set_once(channel):
    ch, _db, calls, _clock = channel
    assert recall(ch, include_global=True) == [("pf", pytest.approx(1.0))]
    assert len(calls) == 1


def test_the_set_is_kept_for_the_ttl_per_scope(channel):
    ch, _db, calls, clock = channel
    recall(ch, include_global=True)
    recall(ch, include_global=True)
    assert len(calls) == 1
    recall(ch, include_global=True, include_shared=True)  # another scope: its own entry
    assert len(calls) == 2
    clock.now += ACTIVE_TTL_S + 1
    recall(ch, include_global=True)
    assert len(calls) == 3


def test_a_picture_save_or_delete_drops_the_cached_set(channel):
    ch, _db, calls, _clock = channel
    recall(ch, include_global=True)
    ch._store_factory().delete_vectors(mid(1))  # any media write bumps the generation
    assert recall(ch, include_global=True) == []
    assert len(calls) == 2


def test_a_scope_change_drops_the_cached_set(channel):
    ch, db, calls, _clock = channel
    assert recall(ch, include_global=True)
    db.update_fact("pf", {"scope": "personal"})
    assert recall(ch, include_global=True) == []
    assert len(calls) == 2


def test_bump_generation_is_monotonic():
    from superlocalmemory.media.generation import generation

    before = generation()
    bump_generation()
    assert generation() > before
