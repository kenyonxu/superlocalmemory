# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""What an operation's audit entry records about who did it and to what.

4.1.25 audit L-16 / G-L2: every store entry carried an empty content hash and
only a capability digest as its actor, so "Chain verified" proved the order of
events and nothing about what was written or by which agent. The hook context
now carries the content hash and the connection's agent; this picks the
fields an audit entry keeps. Only identifiers and hashes: never memory text.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Hook-context keys copied into the audit entry's metadata, as they are.
_KEPT = (
    "ingestion_operation_id", "fact_id", "source_agent_id", "connection_agent",
    "claimed_agent_id", "user_id", "fact_count",
)
_MAX_FACT_IDS = 50


def content_hash(text: str | None) -> str:
    """``sha256:<hex>`` of the text, or ``""`` when there is none."""
    if not text:
        return ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_metadata(ctx: dict[str, Any]) -> dict[str, Any]:
    meta = {key: ctx[key] for key in _KEPT if ctx.get(key) not in (None, "")}
    fact_ids = ctx.get("fact_ids")
    if isinstance(fact_ids, (list, tuple)) and fact_ids:
        meta["fact_ids"] = [str(f) for f in list(fact_ids)[:_MAX_FACT_IDS]]
        if len(fact_ids) > _MAX_FACT_IDS:
            meta["fact_ids_truncated"] = len(fact_ids)
    return meta


__all__ = ["audit_metadata", "content_hash"]
