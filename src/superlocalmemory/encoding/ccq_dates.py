# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Date handling for cognitive consolidation (CCQ): one UTC contract."""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["_parse_date", "_temporal_midpoint"]


def _parse_date(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 date or timestamp to an aware UTC datetime.

    Accepts date-only, ``T`` or space separators, fractional seconds, ``Z``
    and numeric offsets; a naive value is taken as UTC. The previous parser
    tried four offset-free formats, so every ``...+00:00`` timestamp SLM
    writes came back None and was then treated as "close enough" to every
    other date. None now means unknown, never compatible.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _temporal_midpoint(dates: list[datetime]) -> str:
    """ISO-8601 (UTC) midpoint of known dates; empty when none are known."""
    if not dates:
        return ""
    mid = sum(d.timestamp() for d in dates) / len(dates)
    return datetime.fromtimestamp(mid, timezone.utc).isoformat()
