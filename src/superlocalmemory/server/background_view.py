"""Read the already-recorded outputs of summary and tidy-up work."""

from __future__ import annotations

import json
import sqlite3


def summary_changes(conn, profile: str, limit: int) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT summary_id,content,generated_by,source_fact_ids,created_at "
            "FROM consolidated_summaries WHERE profile_id=? ORDER BY created_at DESC LIMIT ?",
            (profile, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    result = []
    for row in rows:
        try:
            ids = json.loads(row["source_fact_ids"] or "[]")
        except (ValueError, TypeError):
            ids = []
        result.append(
            {
                "timestamp": row["created_at"],
                "action_type": "summary written",
                "reason": row["content"],
                "generated_by": row["generated_by"],
                "summary_id": row["summary_id"],
                "source_fact_ids": ids,
            }
        )
    return result
