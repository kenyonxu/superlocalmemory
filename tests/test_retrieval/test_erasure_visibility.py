"""Erasure wins over retained facts, projections and warmed channel caches."""

from __future__ import annotations

import time

import pytest
from benchmark.recall_channels import seed

from superlocalmemory.core.delete_refusal import STALE_AFTER_S, _drop_stale_erasure
from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.retrieval.semantic_channel import SemanticChannel
from superlocalmemory.retrieval.spreading_activation import (
    SpreadingActivation,
    SpreadingActivationConfig,
)
from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage.database import DatabaseManager

HIDDEN = "f000000"
CONTROL = "f000010"


@pytest.fixture()
def store(tmp_path):
    db = DatabaseManager(tmp_path / "synthetic.db")
    seed(db, 20, vectors=True, migrated=True)
    db.upsert_fact_expansion(HIDDEN, "coppercompass")
    db.upsert_fact_expansion(CONTROL, "coppercompass")
    yield db
    db.close()


def tombstone(db, fid=HIDDEN, profile="default"):
    db.execute(
        "INSERT INTO projection_tombstones(profile_id,fact_id,erasure_id,created_at) "
        "VALUES (?, ?, 'synthetic-erasure', ?)",
        (profile, fid, time.time() - STALE_AFTER_S - 1),
    )


def _channel_read(db, name):
    if name in {"keyword", "expansion", "legacy_keyword"}:
        channel = BM25Channel(db)
        if name == "legacy_keyword":
            db.execute("DROP TABLE atomic_facts_fts")
        query = "coppercompass" if name == "expansion" else "memory release"
        return lambda: channel.search(query, "default", top_k=30)
    if name == "semantic":
        channel = SemanticChannel(db)
        return lambda: channel.search([0.01] * 768, "default", top_k=30)
    if name == "entity":
        channel = EntityGraphChannel(db)
        return lambda: channel.search("Alice", "default", top_k=30)
    if name.startswith("temporal"):
        channel = TemporalChannel(db)
        if name == "temporal_recency":
            db.execute("UPDATE atomic_facts SET created_at = datetime('now')")
            return lambda: channel.search("current work", "default", query_type="recency")
        query = "Alice" if name == "temporal_entity" else "2025-01-01T00:00:00"
        return lambda: channel.search(query, "default", top_k=30)
    if name == "graph":
        channel = SpreadingActivation(db, None, SpreadingActivationConfig(top_m=30))
        return lambda: channel.search([0.01] * 768, "default", top_k=30)
    raise AssertionError(name)


@pytest.mark.parametrize("channel", [
    "keyword", "expansion", "legacy_keyword", "semantic", "entity",
    "temporal_date", "temporal_entity", "temporal_recency", "graph",
])
def test_every_channel_rechecks_erasure_after_warming_and_heal(store, channel):
    read = _channel_read(store, channel)
    before = {fid for fid, _ in read()}
    assert {HIDDEN, CONTROL} <= before, (channel, before)
    tombstone(store)
    during = {fid for fid, _ in read()}
    assert HIDDEN not in during, (channel, during)
    assert CONTROL in during
    # This is the transactional tombstone-removal step used by refused-delete
    # heal. The protected-delete test exercises its projection rebuild as well.
    assert _drop_stale_erasure(store, "default", HIDDEN)
    assert {HIDDEN, CONTROL} <= {fid for fid, _ in read()}


@pytest.mark.parametrize("reader", [
    "bulk", "hydration", "ids", "search", "count", "pins", "memory", "shared",
    "repair_opt_in", "embeddings",
])
def test_storage_display_paths_observe_tombstones_and_heal(store, reader):
    store.execute("UPDATE atomic_facts SET pinned=1, scope='global'")
    reads = {
        "bulk": lambda: {f.fact_id for f in store.get_all_facts("default")},
        "hydration": lambda: {f.fact_id for f in store.get_facts_by_ids(
            [HIDDEN, CONTROL], "default")},
        "repair_opt_in": lambda: {f.fact_id for f in store.get_facts_by_ids(
            [HIDDEN, CONTROL], "default", include_quarantined=True)},
        "ids": lambda: store.visible_fact_ids([HIDDEN, CONTROL], "default"),
        "search": lambda: {f.fact_id for f in store.search_facts_fts(
            "memory", "default", limit=30)},
        "pins": lambda: {f.fact_id for f in store.get_pinned("default")},
        "memory": lambda: {f.fact_id for f in store.get_facts_by_memory_id(
            "default", "default")},
        "shared": lambda: {f.fact_id for f in store.get_external_visible_facts(
            "viewer", include_global=True)},
        "embeddings": lambda: {fid for fid, _ in store.get_fact_embeddings_by_ids(
            [HIDDEN, CONTROL], "default")},
        "count": lambda: store.get_fact_count("default"),
    }
    read = reads[reader]
    before = read()
    if reader != "count":
        assert {HIDDEN, CONTROL} <= before
    tombstone(store)
    during = read()
    if reader == "count":
        assert during == before - 1
    else:
        assert HIDDEN not in during and CONTROL in during
    assert store.get_fact(HIDDEN, "default") is not None  # writer still owns the row
    assert _drop_stale_erasure(store, "default", HIDDEN)
    assert read() == before


def test_another_profiles_tombstone_does_not_hide_the_fact(store):
    tombstone(store, profile="viewer")
    assert HIDDEN in store.visible_fact_ids([HIDDEN], "default")


def test_visibility_detects_a_later_migration(store):
    store.execute("DROP TABLE projection_tombstones")
    assert HIDDEN in store.visible_fact_ids([HIDDEN], "default")
    from superlocalmemory.storage.migrations.M035_erasure_receipts import apply
    with store.raw_connection() as conn:
        apply(conn)
    tombstone(store)
    assert HIDDEN not in store.visible_fact_ids([HIDDEN], "default")


def test_native_vector_search_refills_slots_and_restores_after_heal(store):
    from superlocalmemory.retrieval.vector_store import VectorStore, VectorStoreConfig

    vectors = VectorStore(store.db_path, VectorStoreConfig(dimension=4))
    if not vectors.available:
        pytest.skip("sqlite-vec extension unavailable")
    assert vectors.upsert(HIDDEN, "default", [1., 0., 0., 0.])
    assert vectors.upsert(CONTROL, "default", [0., 1., 0., 0.])
    def read():
        return vectors.search([1., 0., 0., 0.], top_k=1, profile_id="default")
    assert read()[0][0] == HIDDEN
    tombstone(store)
    assert read()[0][0] == CONTROL
    assert _drop_stale_erasure(store, "default", HIDDEN)
    assert read()[0][0] == HIDDEN
