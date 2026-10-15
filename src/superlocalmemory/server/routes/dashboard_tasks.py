"""Read projections for dashboard activity; no maintenance work is triggered."""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from superlocalmemory.server.routes.helpers import get_active_profile
from superlocalmemory.storage.database import visible_fact_clause_for_connection
from superlocalmemory.storage.read_connection import ReadConnectionFactory

from .dashboard_feeds import router as feeds_router

router = APIRouter()
router.include_router(feeds_router)
logger = logging.getLogger(__name__)


def _root():
    from superlocalmemory.infra.data_root import canonical_data_root

    return canonical_data_root()


def _read_gate(request):
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.server.rbac_enforce import require_permission

    require_permission(request, Permission.READ, profile=get_active_profile())


def _period_rows(start, end, offset, profile):
    path = _root() / "memory.db"
    if not path.exists():
        return [], 0
    conn = ReadConnectionFactory(path).open()
    try:
        clause = visible_fact_clause_for_connection(conn)
        where = ("profile_id=? AND DATE(created_at, ?) BETWEEN ? AND ? "
                 f"AND lifecycle!='archived'{clause}")
        args = (profile, f"{offset:+d} minutes", start.isoformat(), end.isoformat())
        total = conn.execute(f"SELECT COUNT(*) FROM atomic_facts WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT fact_id,content,created_at FROM atomic_facts WHERE {where} "
            "ORDER BY created_at,fact_id LIMIT 500",
            args,
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


@router.get("/api/summary/range")
def period_summary(
    request: Request, start: date, end: date, tz_offset_minutes: int = Query(0, ge=-840, le=840)
):
    _read_gate(request)
    if start > end or (end - start).days > 365:
        raise HTTPException(422, "Choose an ordered period of at most 366 days.")
    profile = get_active_profile()
    try:
        rows, total = _period_rows(start, end, tz_offset_minutes, profile)
    except sqlite3.Error:
        logger.exception("Period summary unavailable")
        raise HTTPException(503, "Your activity could not be read. Try again.") from None
    lines = [f"Activity from {start} to {end}: {total} saved memories."]
    lines.extend(f"{r['created_at']}: {r['content']}" for r in rows[:30])
    return {
        "kind": "range",
        "profile_id": profile,
        "summary": "\n".join(lines),
        "source_fact_ids": [r["fact_id"] for r in rows],
        "source_count": len(rows),
        "coverage": "insufficient" if not total else "partial" if total > 30 else "full",
        "generated_by": "extractive",
        "metadata": {
            "start": str(start),
            "end": str(end),
            "date_basis": "stored_at",
            "tz_offset_minutes": tz_offset_minutes,
            "total_memories": total,
            "truncated": total > len(rows),
        },
    }


@router.post("/api/dashboard/embedding-test")
async def test_local_embedding(request: Request):
    from superlocalmemory.server.rbac_enforce import require_manage

    require_manage(request)
    engine = getattr(request.app.state, "engine", None)
    embedder = getattr(engine, "_embedder", None)
    if embedder is None:
        raise HTTPException(503, "The local embedding worker is unavailable. Restart and retry.")
    try:
        vector = await asyncio.wait_for(
            asyncio.to_thread(embedder.embed, "test local embedding"), 15
        )
        if vector is None or not len(vector) or not all(math.isfinite(float(v)) for v in vector):
            raise ValueError("empty or invalid embedding")
    except Exception:
        logger.exception("Local embedding test failed")
        raise HTTPException(
            503, "The active embedding model could not answer. Restart and retry."
        ) from None
    return {
        "success": True,
        "dimension": len(vector),
        "message": (f"The active embedding worker answered ({len(vector)} dimensions). "
                    "Save a selected model before testing it."),
    }


@router.get("/api/dashboard/background")
def background_activity(request: Request, limit: int = Query(25, ge=1, le=100)):
    _read_gate(request)
    path = _root() / "memory.db"
    if not path.exists():
        return {"activities": [], "available": True}
    conn = ReadConnectionFactory(path).open()
    try:
        rows = conn.execute(
            "SELECT timestamp,action_type,reason,new_fact_id,existing_fact_id "
            "FROM consolidation_log WHERE profile_id=? ORDER BY timestamp DESC LIMIT ?",
            (get_active_profile(), limit),
        ).fetchall()
        from superlocalmemory.server.background_view import summary_changes
        from superlocalmemory.server.consolidation_runner import is_running

        activities = [dict(r) for r in rows] + summary_changes(conn, get_active_profile(), limit)
        activities.sort(key=lambda item: item["timestamp"], reverse=True)
        return {
            "activities": activities[:limit],
            "available": True,
            "running": is_running(),
            "scope": ("Recorded summary outputs and tidy-up actions. "
                      "Dates show when each change was saved."),
        }
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {
                "activities": [],
                "available": True,
                "scope": "No tidy-up history recorded yet.",
            }
        logger.exception("Background activity read failed")
        raise HTTPException(503, "Background activity is temporarily unavailable.") from None
    finally:
        conn.close()
