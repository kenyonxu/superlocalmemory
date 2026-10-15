# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Atomic MCP deltas, separate from replace-on-flush proxy snapshots."""

from dataclasses import replace


def record_compression(db, before, after):
    db._db.execute(
        "INSERT INTO llmcache_kv_counters(name,count) VALUES "
        "('mcp_compress_runs',1),('mcp_compress_saved',?),"
        "('mcp_compress_before',?),('mcp_compress_after',?) "
        "ON CONFLICT(name) DO UPDATE SET count=count+excluded.count",
        (max(0, before - after), max(0, before), max(0, after)),
    )


def snapshot_with_mcp(db, snapshot):
    counters = db.kv_counters_load()
    return replace(
        snapshot,
        compress_runs=snapshot.compress_runs + counters.get("mcp_compress_runs", 0),
        tokens_saved_compress=snapshot.tokens_saved_compress
        + counters.get("mcp_compress_saved", 0),
        compress_bytes_original=snapshot.compress_bytes_original
        + counters.get("mcp_compress_before", 0),
        compress_bytes_after=snapshot.compress_bytes_after + counters.get("mcp_compress_after", 0),
    )
