"""BRN-23/24: one original, tenant/TTL ownership, durable model savings."""

from types import SimpleNamespace

import pytest

from superlocalmemory.mcp.tools_optimize import register_optimize_tools
from superlocalmemory.optimize.compress.ccr import CCRStore
from superlocalmemory.optimize.compress.router import CompressRouter
from superlocalmemory.optimize.metrics.counters import MetricsCollector
from superlocalmemory.optimize.metrics.persistence import MetricsPersistence
from superlocalmemory.optimize.storage.db import CacheDB
from tests.test_mcp.test_tools_optimize import _MockServer


@pytest.fixture
def compression(tmp_path, monkeypatch):
    db = CacheDB(tmp_path / "cache.db")
    ccr = CCRStore()
    ccr._db = db
    router = CompressRouter()
    router._get_config = lambda: SimpleNamespace(
        compress_enabled=True, compress_mode="aggressive", compress_prose=True
    )
    router._get_llmlingua_compressor = lambda: SimpleNamespace(
        compress=lambda text: "short summary"
    )
    router._ccr_store = ccr
    metrics = MetricsCollector()
    router.set_metrics(metrics)
    monkeypatch.setattr(CompressRouter, "get_instance", lambda: router)
    monkeypatch.setattr(CCRStore, "get_instance", lambda: ccr)
    monkeypatch.setattr(CacheDB, "get_default", lambda: db)
    monkeypatch.setattr(MetricsCollector, "get_instance", lambda: metrics)
    monkeypatch.setenv("SLM_AGENT_ID", "caller")
    server = _MockServer()
    register_optimize_tools(server)
    return server._tools, db, metrics


@pytest.mark.asyncio
@pytest.mark.parametrize("reversible", [False, True])
async def test_single_original_owned_by_mcp_tenant_and_ttl(compression, reversible):
    tools, db, _ = compression
    result = await tools["slm_compress"](
        "A long narrative sentence. " * 80, reversible=reversible, ttl_seconds=1
    )
    assert result["strategy"] == "llmlingua2_prose", result
    assert db.ccr_count() == int(reversible)
    if reversible:
        assert (await tools["slm_retrieve"](result["ccr_id"]))["ok"]
        assert db.ccr_get(result["ccr_id"], tenant_id="default") is None
        db._db.execute("UPDATE llmcache_ccr_originals SET ttl_expires=0")
        assert not (await tools["slm_retrieve"](result["ccr_id"]))["ok"]


@pytest.mark.asyncio
async def test_model_compression_updates_durable_savings(compression):
    tools, db, metrics = compression
    await tools["slm_compress"]("A long narrative sentence. " * 80)
    stats = await tools["slm_optimize_stats"]()
    assert stats["compress_runs"] == 1
    assert stats["tokens_saved_compress"] > 0
    loaded = MetricsCollector()
    MetricsPersistence().load(loaded, db)
    from superlocalmemory.optimize.metrics.mcp_compression import snapshot_with_mcp

    assert snapshot_with_mcp(db, loaded.snapshot()).compress_runs == 1


@pytest.mark.asyncio
async def test_mcp_compression_preserves_existing_proxy_totals(compression):
    from superlocalmemory.optimize.storage.db import MetricsSnapshot

    tools, db, _ = compression
    db.metrics_flush(MetricsSnapshot(hits=17, misses=3, compress_runs=8, tokens_saved_compress=100))
    await tools["slm_compress"]("A long narrative sentence. " * 80)
    stats = await tools["slm_optimize_stats"]()
    assert stats["cache_proxy_hits"] == 17
    assert stats["cache_proxy_misses"] == 3
    assert stats["compress_runs"] == 9
    assert stats["tokens_saved_compress"] > 100


@pytest.mark.asyncio
async def test_recovery_copy_preserves_profile_erasure_ownership(compression, monkeypatch):
    from superlocalmemory.optimize.storage.erasure import erase_profile

    monkeypatch.setitem(
        register_optimize_tools.__globals__, "active_profile_id", lambda: "owner-profile"
    )
    tools, db, _ = compression
    result = await tools["slm_compress"]("A long narrative sentence. " * 80)
    row = db._db.execute(
        "SELECT profile_id FROM llmcache_ccr_originals WHERE ccr_id=?",
        (result["ccr_id"],),
    )[0]
    assert row["profile_id"] == "owner-profile"
    erased = erase_profile(db, "owner-profile")
    assert erased.originals == 1 and not erased.failed
    assert not (await tools["slm_retrieve"](result["ccr_id"]))["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty_id", "sqlite_abort", "wrong_bytes", "oversize"])
async def test_reversible_compression_refuses_unrecoverable_output(
    compression, monkeypatch, failure,
):
    tools, db, _ = compression
    content = "A long narrative sentence. " * 80
    store = CCRStore.get_instance()
    if failure == "empty_id":
        monkeypatch.setattr(store, "store", lambda *a, **k: "")
    elif failure == "sqlite_abort":
        db._db.execute(
            "CREATE TRIGGER refuse_ccr BEFORE INSERT ON llmcache_ccr_originals "
            "BEGIN SELECT RAISE(ABORT,'disk write refused'); END"
        )
    elif failure == "wrong_bytes":
        original_store = store.store
        monkeypatch.setattr(store, "store", lambda *a, **k: original_store(b"wrong", **k))
    else:
        content *= 500
    result = await tools["slm_compress"](content, reversible=True)
    assert result["ok"] is False, result
    assert result["compressed"] == content
    assert result["byte_exact"] is True
    assert result["lossy"] is False
    assert result["ccr_id"] is None
    assert (await tools["slm_optimize_stats"]())["compress_runs"] == 0


@pytest.mark.asyncio
async def test_returned_recovery_id_restores_exact_original(compression):
    tools, _, _ = compression
    content = "A long narrative sentence with Unicode: café.\n" * 80
    result = await tools["slm_compress"](content, reversible=True)
    assert result["ok"] and result["lossy"], result
    recovered = await tools["slm_retrieve"](result["ccr_id"])
    assert recovered["ok"] and recovered["content"] == content
