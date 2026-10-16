# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Use the narrow fact join when its additive migration has arrived."""

from __future__ import annotations

from typing import Any


def fact_table(db: Any) -> str:
    """SQLite prefers its old UNIQUE index even when that reads wide overflow pages."""
    try:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type='index' "
                          "AND name='idx_recall_fact_cover'")
        if isinstance(rows, list) and rows and dict(rows[0]).get("name") == "idx_recall_fact_cover":
            return "atomic_facts AS af INDEXED BY idx_recall_fact_cover"
    except Exception:  # legacy/partial store: the unchanged table join remains correct
        pass
    return "atomic_facts AS af"
