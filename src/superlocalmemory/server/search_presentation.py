# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Search results carry the same presentation fields as browsing (4.1.25 UX-12).

The Memories list reads ``project_name``, ``scope`` and ``shared_with`` from the
store; dashboard search returned recall rows without them, and the page filled
the gaps with defaults: a decision shared with another workspace opened as
"personal", "no project", with the Scope control preset to personal, so one
click would have narrowed it. Each result now gets the stored values, read the
same way the list reads them, in one batched query.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_FIELDS_SQL = (
    "SELECT f.fact_id, f.scope, f.shared_with, "
    "(SELECT CASE WHEN json_valid(m.metadata_json) THEN json_extract(m.metadata_json, '$.project')"
    " END FROM memories m WHERE m.memory_id = f.memory_id) AS project_name "
    "FROM atomic_facts f WHERE f.fact_id IN ({marks})"
)


def with_stored_fields(conn: sqlite3.Connection, results: list[dict[str, Any]]) -> list[dict]:
    """``results`` with ``scope``, ``shared_with`` and ``project_name`` from the store."""
    ids = [str(r.get("fact_id")) for r in results if r.get("fact_id")]
    if not ids:
        return results
    rows = conn.execute(_FIELDS_SQL.format(marks=",".join("?" * len(ids))), ids).fetchall()
    stored = {row[0]: row for row in rows}
    out = []
    for result in results:
        row = stored.get(str(result.get("fact_id")))
        if row is None:
            out.append(result)
            continue
        shared = row[2]
        try:
            shared = json.loads(shared) if isinstance(shared, str) and shared else shared
        except ValueError:
            shared = None
        out.append({**result, "scope": row[1] or "personal", "shared_with": shared or [],
                    "project_name": row[3] or result.get("project_name") or ""})
    return out


__all__ = ["with_stored_fields"]
