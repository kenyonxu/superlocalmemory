# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""MCP compression owns recovery and accounting at one caller boundary."""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("slm.mcp.tools_optimize")


@dataclass(frozen=True)
class CompressionContext:
    router: Any
    result: Any
    ccr: Any
    cache: Any
    tenant: str
    max_bytes: int
    profile_id: str


def _compress(ctx, content, mode, notes):
    if mode == "normalize":
        normalized = ctx.router._normalize_whitespace(content)
        return ctx.result(
            compressed_text=normalized,
            strategy="normalize" if normalized != content else "none",
            tokens_before=len(content.split()),
            tokens_after=len(normalized.split()),
            lossy=normalized != content,
        )
    if mode == "aggressive":
        notes.append("aggressive mode requires daemon compress_mode=aggressive in config")
    return ctx.router.get_instance().compress_text(content)


def _recoverable(ctx, content, reversible, ttl, notes):
    if not reversible:
        return None
    original = content.encode("utf-8")
    store = ctx.ccr.get_instance()
    stored = store.store(
        original,
        tenant_id=ctx.tenant,
        ttl_seconds=ttl,
        profile_id=ctx.profile_id,
    )
    if not stored or store.retrieve(stored, tenant_id=ctx.tenant) != original:
        raise RuntimeError("original persistence could not be verified; returning unchanged input")
    notes.append("reversible: call slm_retrieve with this ccr_id")
    return stored


def _result(content, res, ccr_id, notes):
    return {
        "ok": True,
        "compressed": res.compressed_text,
        "strategy": res.strategy,
        "tokens_before": res.tokens_before,
        "tokens_after": res.tokens_after,
        "ratio": round(res.tokens_after / res.tokens_before, 4) if res.tokens_before else 1.0,
        "lossy": res.lossy,
        "byte_exact": res.compressed_text == content,
        "ccr_id": ccr_id,
        "note": " | ".join(notes) or None,
    }


def _failure(content, note):
    text = content if isinstance(content, str) else ""
    tokens = len(text.split())
    return {
        "ok": False,
        "compressed": text,
        "strategy": "none",
        "tokens_before": tokens,
        "tokens_after": tokens,
        "ratio": 1.0,
        "lossy": False,
        "byte_exact": isinstance(content, str),
        "ccr_id": None,
        "note": note,
    }


def compress(ctx, content, mode, reversible, ttl_seconds):
    try:
        if not isinstance(content, str) or not content:
            return _failure(content, "empty input")
        notes = []
        if len(content.encode("utf-8")) > ctx.max_bytes:
            if reversible:
                return _failure(content, "content over 1MB: reversible compression refused")
            notes.append("content over 1MB: irreversible compression requested")
        res = _compress(ctx, content, mode, notes)
        ccr_id = None
        if res.compressed_text != content:
            from superlocalmemory.optimize.metrics.mcp_compression import record_compression

            ccr_id = _recoverable(ctx, content, reversible, ttl_seconds, notes)
            record_compression(ctx.cache.get_default(), res.tokens_before, res.tokens_after)
        return _result(content, res, ccr_id, notes)
    except Exception as exc:
        logger.error("slm_compress failed: %s", exc)
        return _failure(content, f"internal error: {exc}")
