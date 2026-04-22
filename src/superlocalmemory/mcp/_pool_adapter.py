# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""MCP-side adapters onto :class:`WorkerPool`.

The pool returns plain dicts. Hooks (``AutoRecall`` / ``AutoCapture``) and
direct tool callers expect a ``RecallResponse``-shaped object (``.results``
list with ``.fact.content``, ``.fact.fact_id``, ``.score``) and a list of
fact ids. These adapters bridge the two without pulling the heavy engine
into the MCP process.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _pool():
    # Imported lazily so test harnesses can patch WorkerPool.shared.
    from superlocalmemory.core.worker_pool import WorkerPool
    return WorkerPool.shared()


def pool_recall(query: str, limit: int = 10, **_: Any) -> SimpleNamespace:
    """Call pool.recall and reshape its dict into a RecallResponse-like object."""
    raw = _pool().recall(query=query, limit=limit)
    items = raw.get("results", []) if isinstance(raw, dict) else []
    results = [
        SimpleNamespace(
            fact=SimpleNamespace(
                fact_id=item.get("fact_id", ""),
                content=item.get("content", ""),
                memory_id=item.get("memory_id", ""),
            ),
            score=float(item.get("score", 0.0)),
            confidence=float(item.get("confidence", 0.0)),
            trust_score=float(item.get("trust_score", 0.0)),
            channel_scores=item.get("channel_scores", {}) or {},
        )
        for item in items
    ]
    return SimpleNamespace(
        results=results,
        query_type=raw.get("query_type", "") if isinstance(raw, dict) else "",
        retrieval_time_ms=float(raw.get("retrieval_time_ms", 0.0))
        if isinstance(raw, dict) else 0.0,
    )


def pool_store(content: str, metadata: dict | None = None) -> list[str]:
    """Call pool.store and return the fact id list."""
    raw = _pool().store(content=content, metadata=metadata or {})
    if isinstance(raw, dict):
        return list(raw.get("fact_ids", []))
    return []
