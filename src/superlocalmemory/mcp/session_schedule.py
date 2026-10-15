# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Scheduled facts admitted to startup context."""

from __future__ import annotations

from superlocalmemory.mcp import tools_active as shared


def _upcoming_scheduled_facts(
    engine, now: shared.datetime.datetime, profile_id: str | None = None
) -> list[dict]:
    """Facts scheduled from today through the horizon, soonest first.

    Bounded and index-backed, because this runs on every session start. Returns
    an empty list on any failure: a session must still open when this query
    cannot answer, so the caller omits the surface entirely rather than showing
    an empty section that reads like a defect.

    Selects on the stored type of the fact, which is a classification recorded at
    write time. It is unrelated to the similarly named retrieval channel despite
    sharing the word.
    """
    try:
        db = getattr(engine, "_db", None) or getattr(engine, "db", None)
        if db is None or not hasattr(db, "execute"):
            return []
        start = now.date().isoformat()
        end = (
            (now + shared.datetime.timedelta(days=shared._SCHEDULED_HORIZON_DAYS + 1))
            .date()
            .isoformat()
        )
        current = ""
        clause_fn = getattr(type(db), "current_fact_clause", None)
        if callable(clause_fn):
            try:
                current = clause_fn(db)
            except Exception:
                shared.logger.warning("scheduled surface could not resolve its filter")
                current = ""
        rows = db.execute(
            "SELECT fact_id, content, referenced_date FROM atomic_facts "
            "WHERE profile_id = ? AND fact_type = 'prospective' "
            "AND referenced_date IS NOT NULL AND referenced_date >= ? "
            f"AND referenced_date < ? {current} "
            f"ORDER BY referenced_date ASC LIMIT {shared._SCHEDULED_LIMIT}",
            (profile_id or engine.profile_id, start, end),
        )
        return [
            {"fact_id": r["fact_id"], "content": r["content"], "scheduled_at": r["referenced_date"]}
            for r in rows
        ]
    except Exception as exc:
        shared.logger.warning("scheduled-fact surface failed: %s", exc)
        return []
