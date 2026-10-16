# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Erasing a person reaches the MCP cache and the reversible compression originals (OPS-12).

``slm_cache_set`` and ``slm_compress`` keep whatever text an agent routes through
them, encrypted with a key that stays on this computer. Nothing linked a cached
row to a profile, so an erased profile's recalled text could be read back
through ``slm_cache_get`` and ``slm_retrieve`` after the erasure said complete.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

import pytest

from superlocalmemory.optimize.storage import erasure as cache_erasure
from superlocalmemory.optimize.storage.db import CacheDB

TENANT = "t" * 64


@pytest.fixture
def cache(tmp_path):
    db = CacheDB(tmp_path / "llmcache.db")
    yield db
    db.close()


def _put(db: CacheDB, key: str, text: str, profile: str) -> str:
    db.set(key * 64 if len(key) == 1 else key, TENANT, text.encode(), model="mcp-kv",
           ttl_expires=time.time() + 3600, tags=["mcp-kv"], profile_id=profile)
    return key * 64 if len(key) == 1 else key


def _original(db: CacheDB, text: str, profile: str) -> str:
    ccr_id = str(uuid.uuid4())
    db.ccr_put(ccr_id, text.encode(), tenant_id=TENANT, profile_id=profile)
    return ccr_id


def test_a_whole_profile_erasure_removes_its_cache_rows_and_originals(cache) -> None:
    mine = _put(cache, "a", "Alice lives in Lisbon", "alice")
    theirs = _put(cache, "b", "Bob lives in Brno", "bob")
    my_original = _original(cache, "Alice's full note", "alice")
    their_original = _original(cache, "Bob's full note", "bob")

    result = cache_erasure.erase_profile(cache, "alice")

    assert result.entries == 1 and result.originals == 1 and not result.failed
    assert cache.get_value(mine, TENANT) is None, "the erased profile's cached value is readable"
    assert cache.ccr_get(my_original, tenant_id=TENANT) is None, "its original is readable"
    assert cache.get_value(theirs, TENANT) == b"Bob lives in Brno"
    assert cache.ccr_get(their_original, tenant_id=TENANT) == b"Bob's full note"


def test_rows_with_no_recorded_owner_are_removed_too_and_counted(cache) -> None:
    """A row from before provenance existed might be hers; a cache is not worth the risk."""
    unowned = _put(cache, "c", "somebody's text", "")
    result = cache_erasure.erase_profile(cache, "alice")

    assert cache.get_value(unowned, TENANT) is None
    assert result.unattributed == 1
    assert result.as_counts()["optimize_cache_unattributed"] == 1


def test_a_targeted_erasure_removes_only_rows_holding_the_erased_words(cache) -> None:
    holds = _put(cache, "d", "Notes: Alice Zebrowski moved to Lisbon in May", "alice")
    other = _put(cache, "e", "Notes: the quarterly deploy window is Tuesday", "alice")
    holds_original = _original(cache, "Alice Zebrowski moved to Lisbon in May, in full", "alice")

    result = cache_erasure.erase_profile(cache, "alice", needles=["Alice Zebrowski"])

    assert cache.get_value(holds, TENANT) is None
    assert cache.ccr_get(holds_original, tenant_id=TENANT) is None
    assert cache.get_value(other, TENANT) is not None, "unrelated cache survives"
    assert result.entries == 1 and result.originals == 1


def test_no_needles_means_nothing_to_do_not_everything(cache) -> None:
    kept = _put(cache, "f", "keep me", "alice")
    result = cache_erasure.erase_profile(cache, "alice", needles=[])
    assert result.entries == 0 and cache.get_value(kept, TENANT) is not None


def test_the_file_is_rewritten_so_deleted_values_leave_free_pages(cache, tmp_path) -> None:
    marker = "ZQXCACHE-" + "x" * 40
    key = _put(cache, "g", marker, "alice")
    # Plaintext copies are what a free page would leak: store the marker where SQLite can see it.
    cache._db.execute("UPDATE llmcache_entries SET tag_json = ? WHERE cache_key = ?",
                      (marker, key))
    cache._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert marker.encode() in (tmp_path / "llmcache.db").read_bytes()

    cache_erasure.erase_profile(cache, "alice")

    assert marker.encode() not in (tmp_path / "llmcache.db").read_bytes()
    wal = tmp_path / "llmcache.db-wal"
    assert not wal.exists() or marker.encode() not in wal.read_bytes()


# ------------------------------------------------------------------ migration

_OLD_ENTRIES = """
CREATE TABLE llmcache_entries (
    entry_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'default',
    model TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', value_blob BLOB NOT NULL,
    compressed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT 'x', last_hit_at TEXT,
    ttl_expires REAL, hit_count INTEGER NOT NULL DEFAULT 0, byte_size INTEGER NOT NULL DEFAULT 0,
    tag_json TEXT NOT NULL DEFAULT '[]', cache_tier TEXT NOT NULL DEFAULT 'exact');
CREATE UNIQUE INDEX idx_llmcache_key_tenant ON llmcache_entries (cache_key, tenant_id);
CREATE TABLE llmcache_ccr_originals (
    ccr_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default', original_blob BLOB NOT NULL,
    compressed_hash TEXT NOT NULL, byte_size_orig INTEGER NOT NULL DEFAULT 0,
    byte_size_comp INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT 'x', ttl_expires REAL);
"""


def test_an_older_cache_file_gains_the_provenance_column_without_losing_a_row(tmp_path) -> None:
    path = tmp_path / "llmcache.db"
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_ENTRIES)
    rows = [(f"e{i}", f"k{i}", "t", b"blob") for i in range(2000)]
    conn.executemany(
        "INSERT INTO llmcache_entries (entry_id, cache_key, tenant_id, value_blob) "
        "VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    CacheDB(path).close()  # opening upgrades it, like any release

    conn = sqlite3.connect(path)
    try:
        for table in ("llmcache_entries", "llmcache_ccr_originals"):
            assert "profile_id" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert conn.execute("SELECT COUNT(*) FROM llmcache_entries").fetchone()[0] == 2000
        assert conn.execute(
            "SELECT COUNT(DISTINCT profile_id) FROM llmcache_entries").fetchone()[0] == 1
    finally:
        conn.close()
    CacheDB(path).close()  # and a second open is a no-op


# ------------------------------------------------------ the MCP tools record it

class _Server:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


async def test_the_mcp_tools_record_the_active_profile(cache, tmp_path, monkeypatch) -> None:
    import json

    from superlocalmemory.mcp import tools_optimize
    from superlocalmemory.optimize.compress.ccr import CCRStore

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    (tmp_path / "profiles.json").write_text(json.dumps({"active_profile": "alice"}))
    monkeypatch.setattr(tools_optimize, "CacheDB",
                        type("F", (), {"get_default": staticmethod(lambda: cache)}))
    store = CCRStore()
    store._db = cache
    monkeypatch.setattr(CCRStore, "get_instance", classmethod(lambda cls: store))
    server = _Server()
    tools_optimize.register_optimize_tools(server)

    stored = await server.tools["slm_cache_set"](key="recall:alice", value="Alice's address")
    assert stored["stored"] is True
    long_text = " ".join(f"Alice sentence number {i} with filler words." for i in range(400))
    compressed = await server.tools["slm_compress"](content=long_text, mode="auto")

    rows = cache._db.execute("SELECT profile_id FROM llmcache_entries")
    assert [dict(r)["profile_id"] for r in rows] == ["alice"]
    if compressed.get("ccr_id"):
        originals = cache._db.execute("SELECT profile_id FROM llmcache_ccr_originals")
        assert [dict(r)["profile_id"] for r in originals] == ["alice"]
