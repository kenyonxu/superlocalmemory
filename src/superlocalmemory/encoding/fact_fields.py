# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Small constructors for extracted fact fields, shared by the parser strategies."""

from __future__ import annotations

from superlocalmemory.encoding.date_resolution import try_parse_date
from superlocalmemory.storage.models import FactType

_TYPES = {
    "episodic": FactType.EPISODIC,
    "experience": FactType.EPISODIC,
    "semantic": FactType.SEMANTIC,
    "world": FactType.SEMANTIC,
    "opinion": FactType.OPINION,
    "prospective": FactType.PROSPECTIVE,
    "temporal": FactType.PROSPECTIVE,
}


def llm_type(item: dict) -> FactType:
    return _TYPES.get(
        str(item.get("fact_type", item.get("type", "semantic"))).lower(), FactType.SEMANTIC
    )


def llm_dates(item: dict, session_date: str | None) -> dict:
    values = {
        "referenced_date": item.get("referenced_date") or item.get("date"),
        "interval_start": item.get("interval_start"),
        "interval_end": item.get("interval_end"),
    }
    return {
        name: try_parse_date(str(value), session_date) if value else None
        for name, value in values.items()
    }


def unit_score(value, default: float, scale: float = 1.0) -> float:
    try:
        return min(1.0, max(0.0, float(value) / scale))
    except (TypeError, ValueError):
        return default


def statement_shape(text: str) -> bool:
    """Reject obvious fragments and bare code expressions; retain natural source statements."""
    import re

    if len(text.split()) < 3 or text.rstrip().endswith(":"):
        return False
    if re.match(r"^(?:at|in|on|from|with|by)\b", text, re.I) and len(text.split()) <= 4:
        return False
    if re.search(r"==|<=|>=|\*|\{|\}|(?<![<>=!])=(?!=)", text):
        return False
    if re.match(r"^assert\b", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))
