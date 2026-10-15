"""Bounded database projections for the dashboard's heavy feeds."""

from __future__ import annotations

import json
from contextlib import contextmanager

from fastapi import APIRouter, Query, Request

from superlocalmemory.infra.data_root import state_path
from superlocalmemory.server.routes.helpers import get_active_profile
from superlocalmemory.storage.read_connection import ReadConnectionFactory

router = APIRouter(prefix="/api/dashboard")


@contextmanager
def _read(request):
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.server.rbac_enforce import require_permission

    profile = get_active_profile()
    require_permission(request, Permission.READ, profile=profile)
    conn = ReadConnectionFactory(state_path("memory.db")).open()
    try:
        yield conn, profile
    finally:
        conn.close()


def _entities(raw):
    try:
        return json.loads(raw or "[]")[:5]
    except (ValueError, TypeError):
        return []


@router.get("/clusters")
def clusters(
    request: Request,
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("members-desc", pattern="^(members-desc|members-asc|recent-desc)$"),
):
    with _read(request) as (conn, profile):
        order = {
            "members-desc": "member_count DESC,cluster_id",
            "members-asc": "member_count,cluster_id",
            "recent-desc": "first_memory DESC,cluster_id",
        }[sort]
        where = "profile_id=? AND json_valid(fact_ids_json) AND json_array_length(fact_ids_json)>0"
        total = conn.execute(
            f"SELECT COUNT(*) FROM memory_scenes WHERE {where}", (profile,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT scene_id AS cluster_id,theme AS summary,theme AS categories,"
            "json_array_length(fact_ids_json) AS member_count,entity_ids_json,"
            "created_at AS first_memory "
            f"FROM memory_scenes WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (profile, limit, offset),
        ).fetchall()
        records = [{**dict(r), "top_entities": _entities(r["entity_ids_json"])} for r in rows]
        for r in records:
            r.pop("entity_ids_json", None)
        return {
            "clusters": records,
            "total_clusters": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(records) < total,
            "profile": profile,
        }


@router.get("/trust")
def trust(
    request: Request,
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("score-desc", pattern="^(score-desc|score-asc|evidence-desc|updated-desc)$"),
):
    with _read(request) as (conn, profile):
        order = {
            "score-desc": "trust_score DESC,target_id",
            "score-asc": "trust_score,target_id",
            "evidence-desc": "evidence_count DESC,target_id",
            "updated-desc": "last_updated DESC,target_id",
        }[sort]
        counts = conn.execute(
            "SELECT COUNT(*) AS total,AVG(trust_score) AS avg_trust,"
            "SUM(CASE WHEN trust_score<0.3 THEN 1 ELSE 0 END) AS alert_count "
            "FROM trust_scores WHERE profile_id=?",
            (profile,),
        ).fetchone()
        rows = conn.execute(
            "SELECT target_id,target_type,trust_score,evidence_count,last_updated "
            "FROM trust_scores "
            f"WHERE profile_id=? ORDER BY {order} LIMIT ? OFFSET ?",
            (profile, limit, offset),
        ).fetchall()
        return {
            "agents": [dict(r) for r in rows],
            "total": counts["total"],
            "avg_trust": counts["avg_trust"],
            "alert_count": counts["alert_count"] or 0,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < counts["total"],
            "profile": profile,
        }
