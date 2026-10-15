# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A memory saved a minute ago is being indexed, not missing from the index.

``slm doctor`` on the author's store (13 Oct 2026) warned "1 memories are saved
but missing from the search index" and counted 9 more with no vector. Every one
had been saved in the previous half hour on a loaded machine; the background
materializer embedded and indexed each a minute or two later, and new saves
took their place. A warning that is always on is one nobody reads. Memories
inside a short grace window are reported as being indexed; older ones still
warn, so a stuck index is still caught.
"""

from __future__ import annotations

import pytest

from superlocalmemory.core import memory_health
from superlocalmemory.core.memory_health import classify, describe, measure
from superlocalmemory.retrieval.vector_store import VectorStore, VectorStoreConfig
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)

DIM = 8
OLD = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "memory.db"
    db = DatabaseManager(path)
    db.initialize(schema)
    db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('p', 'p')")
    vectors = VectorStore(path, VectorStoreConfig(dimension=DIM))
    for i in range(4):
        db.store_memory(MemoryRecord(memory_id=f"m{i}", profile_id="p", content=f"c{i}"))
        db.store_fact(AtomicFact(fact_id=f"f{i}", memory_id=f"m{i}", profile_id="p",
                                 content=f"fact {i}", embedding=[0.1 * (i + 1)] * DIM))
        vectors.upsert(f"f{i}", "p", [0.1 * (i + 1)] * DIM)
    db.execute("UPDATE atomic_facts SET created_at = ?", (OLD,))
    return db, path


def _save(db, fid, *, embedding, created_at=None):
    db.store_memory(MemoryRecord(memory_id=f"m-{fid}", profile_id="p", content=fid))
    db.store_fact(AtomicFact(fact_id=fid, memory_id=f"m-{fid}", profile_id="p",
                             content=f"fact {fid}", embedding=embedding))
    if created_at:
        db.execute("UPDATE atomic_facts SET created_at = ? WHERE fact_id = ?", (created_at, fid))


def test_just_saved_memories_are_being_indexed(store):
    db, path = store
    _save(db, "new-vector-pending", embedding=None)
    _save(db, "new-index-pending", embedding=[0.5] * DIM)
    health = measure(path)
    assert health.indexing == 2
    assert health.missing_vector == 0 and health.missing_projection == 0
    assert health.healthy and classify(health) == ("PASS", "")
    lines = " ".join(describe(health))
    assert "missing from the search index" not in lines
    assert "2 memories saved in the last few minutes are still being indexed" in lines
    assert "All 6 of them can be found" not in lines


def test_an_old_memory_without_a_vector_still_warns(store):
    db, path = store
    _save(db, "stuck-vector", embedding=None, created_at=OLD)
    _save(db, "stuck-index", embedding=[0.5] * DIM, created_at=OLD)
    health = measure(path)
    assert health.indexing == 0
    assert health.missing_vector == 1 and health.missing_projection == 1
    assert not health.healthy
    assert "missing from the search index" in " ".join(describe(health))


def test_the_grace_window_is_short(store, monkeypatch):
    assert memory_health.INDEXING_GRACE_MINUTES <= 15
