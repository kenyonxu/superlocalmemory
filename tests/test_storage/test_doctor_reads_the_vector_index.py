# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``slm doctor`` counts memories whose vector is physically in the index (OPS-20).

It used to count ``embedding_metadata`` rows joined to facts, and the metadata
survives the loss of the vectors it describes: with the index emptied it still
printed "All 303 of them can be found by asking a question".
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.core.memory_health import classify, describe, measure
from superlocalmemory.retrieval import vector_physical
from superlocalmemory.retrieval.vector_store import VectorStore, VectorStoreConfig
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)

DIM = 8


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "memory.db"
    db = DatabaseManager(path)
    db.initialize(schema)
    db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('p', 'p')")
    vectors = VectorStore(path, VectorStoreConfig(dimension=DIM))
    assert vectors.available
    for i in range(5):
        db.store_memory(MemoryRecord(memory_id=f"m{i}", profile_id="p", content=f"c{i}"))
        db.store_fact(AtomicFact(fact_id=f"f{i}", memory_id=f"m{i}", profile_id="p",
                                 content=f"fact {i}", embedding=[0.1 * (i + 1)] * DIM))
        assert vectors.upsert(f"f{i}", "p", [0.1 * (i + 1)] * DIM)
    # Settled memories: a fact saved moments ago is still being indexed
    # (test_recent_memories_are_being_indexed_not_missing.py).
    db.execute("UPDATE atomic_facts SET created_at = '2026-01-01T00:00:00+00:00'")
    return path, vectors


def _lose_physical_vectors(path, only: str | None = None) -> None:
    conn = vector_physical.open_vec(path)
    try:
        if only:
            rowid = conn.execute(
                "SELECT vec_rowid FROM embedding_metadata WHERE fact_id = ?", (only,)).fetchone()[0]
            conn.execute("DELETE FROM fact_embeddings WHERE rowid = ?", (rowid,))
        else:
            conn.execute("DELETE FROM fact_embeddings")
        conn.commit()
    finally:
        conn.close()


def test_a_healthy_store_reports_every_memory_findable(store) -> None:
    path, _ = store
    health = measure(path)
    assert (health.live_facts, health.findable_by_meaning) == (5, 5)
    assert health.missing_projection == 0 and health.index_verified and health.healthy


def test_an_emptied_index_is_not_reported_as_all_findable(store) -> None:
    path, _ = store
    _lose_physical_vectors(path)

    health = measure(path)

    assert health.findable_by_meaning == 0, "counted the surviving metadata, not the index"
    assert health.missing_projection == 5 and not health.healthy
    lines = " ".join(describe(health))
    assert "All 5" not in lines and "0 of them" in lines
    assert "missing from the search index" in lines
    assert classify(health) == ("FAIL", "slm restart")


def test_one_lost_vector_is_one_missing_projection(store) -> None:
    path, _ = store
    _lose_physical_vectors(path, only="f2")

    health = measure(path)

    assert (health.findable_by_meaning, health.missing_projection) == (4, 1)
    assert not health.healthy


def test_metadata_under_the_wrong_profile_is_not_findable(store) -> None:
    path, _ = store
    conn = sqlite3.connect(path)
    conn.execute("UPDATE embedding_metadata SET profile_id = 'other' WHERE fact_id = 'f1'")
    conn.commit()
    conn.close()

    assert measure(path).findable_by_meaning == 4


def test_an_index_this_program_cannot_open_is_unverified_not_healthy(store, monkeypatch) -> None:
    path, _ = store

    def _no_extension(*_a, **_k):
        raise vector_physical.VectorIndexUnreadable("sqlite-vec cannot be loaded here")

    monkeypatch.setattr(vector_physical, "open_vec", _no_extension)

    health = measure(path)

    assert health.index_verified is False and "fact_embeddings" in health.unavailable
    assert not health.healthy, "an unchecked index must not be reported healthy"
    assert "could not be checked" in " ".join(describe(health))
    assert classify(health)[0] == "WARN"


def test_the_sweep_empties_a_profiles_partition_and_reads_it_back(store) -> None:
    path, _ = store
    conn = vector_physical.open_vec(path)
    before = len(vector_physical.physical_rowids(conn, "p"))
    conn.close()

    outcome = vector_physical.purge_profile(path, "p")

    assert before == 5 and outcome.removed == 5 and outcome.remaining == 0
    conn = vector_physical.open_vec(path)
    try:
        assert vector_physical.physical_rowids(conn, "p") == []
        assert conn.execute("SELECT COUNT(*) FROM embedding_metadata").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_sweep_removes_rows_nothing_points_at(store) -> None:
    path, _ = store
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM embedding_metadata")
    conn.execute("DELETE FROM vector_row_map")
    conn.commit()
    conn.close()

    outcome = vector_physical.purge_profile(path, "p")

    assert outcome.remaining == 0 and outcome.removed == 5, "unmapped rows are exactly what leaks"
