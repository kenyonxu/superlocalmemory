# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Layered recall: depth 0/1/2 and a max_tokens budget that is never exceeded."""

from __future__ import annotations

import pytest

from superlocalmemory.core import recall_layers as rl
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


def _items(n=5, chars=400):
    return [{"fact_id": f"f{i}", "memory_id": f"m{i}", "tier": "supported", "score": 0.8,
             "content": f"memory {i} " + "word " * (chars // 5), "channel_scores": {}}
            for i in range(n)]


def test_depths_parse_and_refuse_anything_else() -> None:
    assert rl.parse_depth(None) == 1 and rl.parse_depth("0") == 0 and rl.parse_depth(2) == 2
    with pytest.raises(ValueError):
        rl.parse_depth(3)
    assert rl.parse_max_tokens("") is None and rl.parse_max_tokens("200") == 200


def test_glance_is_ids_tier_and_a_one_line_preview() -> None:
    items, report = rl.shape(_items(2), depth=rl.GLANCE)
    assert set(items[0]) <= {"fact_id", "memory_id", "tier", "score", "content",
                             "rank_position", "weak", "support"}
    assert len(items[0]["content"]) <= rl.PREVIEW_CHARS and "\n" not in items[0]["content"]
    assert report["depth"] == 0


@pytest.mark.parametrize("budget", [40, 120, 300, 1000])
@pytest.mark.parametrize("depth", [0, 1, 2])
def test_max_tokens_is_never_exceeded(budget, depth) -> None:
    ctx = lambda item: {"source": "s " * 500, "same_session": [], "same_entities": []}  # noqa: E731
    items, report = rl.shape(_items(6), depth=depth, max_tokens=budget, context_of=ctx)
    assert rl.tokens_of(items) <= budget
    assert report["trimmed"] == (len(items) < 6 or rl.tokens_of(_items(6)) > budget
                                 or depth == 2)


def test_without_a_budget_depth_one_is_unchanged() -> None:
    items, report = rl.shape(_items(3))
    assert items == _items(3) and not report["trimmed"]


def test_a_failing_context_never_loses_the_memory() -> None:
    def boom(item):
        raise RuntimeError("db down")
    items, _ = rl.shape(_items(1), depth=rl.CONTEXT, context_of=boom)
    assert items[0]["context"] == {} and items[0]["fact_id"] == "f0"


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(tmp_path / "memory.db")
    manager.initialize(schema)
    manager.execute("INSERT OR IGNORE INTO profiles (profile_id, name, description) "
                    "VALUES ('other', 'other', '')")
    return manager


def _fact(db, fid, mid, content, *, session="s1", entities=(), profile="default"):
    if not db.execute("SELECT 1 FROM memories WHERE memory_id = ?", (mid,)):
        db.store_memory(MemoryRecord(memory_id=mid, content=f"source of {mid}",
                                     profile_id=profile))
    db.store_fact(AtomicFact(fact_id=fid, memory_id=mid, content=content, session_id=session,
                             canonical_entities=list(entities), profile_id=profile))


def test_depth_two_brings_source_session_and_entity_neighbours_through_the_fetch_gate(
        db) -> None:
    _fact(db, "f1", "m1", "Kestrel uses PostgreSQL 16.", entities=["e-kestrel"])
    _fact(db, "f2", "m2", "Kestrel backups run daily.", entities=["e-kestrel"], session="s9")
    _fact(db, "f3", "m3", "The retro is on Thursday.")
    _fact(db, "f4", "m4", "Other profile note.", profile="other", entities=["e-kestrel"])
    ctx = rl.context_for(db, "default", "f1")
    assert ctx["source"] == "source of m1"
    assert [r["fact_id"] for r in ctx["same_session"]] == ["f3"]
    assert [r["fact_id"] for r in ctx["same_entities"]] == ["f2"]
    assert ctx["superseded_by"] is None
    assert rl.context_for(db, "default", "f4") == {}  # another profile's fact
    hidden = rl.context_for(db, "default", "f1", visible=lambda facts: [])
    assert hidden["same_session"] == [] and hidden["same_entities"] == []
