"""Visual search respects visible scopes and bounds candidate work."""
from unittest.mock import MagicMock

import pytest

from superlocalmemory.retrieval.media_channel import MediaChannel
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

from ._media_support import FakeClient, make_store, mid, unit


def test_picture_candidate_cost_is_capped():
    store, db = MagicMock(), MagicMock()
    store.knn.return_value = []
    store.memory_ids_of.return_value = {}
    ch = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db)
    ch.search(unit(0), "default", 10000)
    assert store.knn.call_args.args[2] <= 32


def test_per_space_floor_applies_before_picture_fusion():
    from types import SimpleNamespace

    store, db = MagicMock(), MagicMock()
    store.knn.return_value = [("weak", .3), ("strong", .1)]
    store.memory_ids_of.side_effect = lambda ids: {key: [key] for key in ids}
    db.execute.side_effect = lambda sql, args: [
        {"fact_id": mid, "memory_id": mid} for mid in args[1:]]
    ch = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db,
                      plan_factory=lambda: SimpleNamespace(min_score=.8))
    assert ch.search(unit(0), "default", 10) == [("strong", pytest.approx(.9))]
    assert store.memory_ids_of.call_args.args[0] == ["strong"]


@pytest.mark.parametrize("scope,audience,global_on,shared_on,expected", [
    ("global", [], True, False, True),
    ("global", [], False, False, False),
    ("shared", ["reader"], False, True, True),
    ("shared", ["someone-else"], False, True, False),
    ("personal", [], True, True, False),
])
def test_reader_with_no_own_pictures_searches_only_visible_scopes(
    tmp_path, scope, audience, global_on, shared_on, expected,
):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    store = make_store(tmp_path, [(mid(1), "image", "picture", unit(0))])
    db.store_memory(MemoryRecord(memory_id="picture", profile_id="default", content="picture",
                                metadata={"_slm_source": {"type": "media", "media_id": mid(1)}}))
    db.store_fact(AtomicFact(fact_id="picture-fact", memory_id="picture", content="picture",
                            profile_id="default", scope=scope, shared_with=audience))
    ch = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db)
    flags = {"include_global": global_on, "include_shared": shared_on}
    try:
        assert ch.is_active("reader", **flags) == expected
        found = ch.search(unit(0), "reader", 10, **flags)
        assert bool(found) == expected
        if found:
            assert found[0][0] == "picture-fact"
        db.execute("UPDATE atomic_facts SET scope='personal' WHERE fact_id='picture-fact'")
        assert ch.search(unit(0), "reader", 10, **flags) == []
    finally:
        store.close()
        db.close()


@pytest.mark.parametrize("flags,profile", [
    ({}, "default"),
    ({"include_global": True}, "reader"),
])
def test_a_picture_with_many_facts_cannot_crowd_out_the_right_picture(tmp_path, flags, profile):
    from ._media_support import blend

    scope = "global" if flags else "personal"
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    store = make_store(tmp_path, [(mid(1), "image", "pic-a", unit(0)),
                                  (mid(2), "image", "pic-b", blend(0, 1, .3))])
    try:
        for memory, number in (("pic-a", 1), ("pic-b", 2)):
            source = {"type": "media", "media_id": mid(number)}
            db.store_memory(MemoryRecord(memory_id=memory, profile_id="default", content=memory,
                                         metadata={"_slm_source": source}))
        for n in range(40):
            db.store_fact(AtomicFact(fact_id=f"a-{n:02d}", memory_id="pic-a", content=f"a {n}",
                                     profile_id="default", scope=scope))
        db.store_fact(AtomicFact(fact_id="b-0", memory_id="pic-b", content="b",
                                 profile_id="default", scope=scope))
        ch = MediaChannel(lambda: store, lambda: FakeClient(unit(0)), db)
        found = ch.search(unit(0), profile, 50, **flags)
        ids = [fact_id for fact_id, _ in found]
        assert "b-0" in ids
        assert sum(1 for i in ids if i.startswith("a-")) <= 3
    finally:
        store.close()
        db.close()
