# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Lifecycle Routes
 - AGPL-3.0-or-later

Routes: /api/lifecycle/status, /api/lifecycle/compact
Uses V3 compliance.lifecycle.LifecycleManager.
"""

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from superlocalmemory.server.endpoint_cache import cached
from superlocalmemory.server.route_mutations import authorize_route_mutation

from .helpers import DB_PATH, get_active_profile, get_engine_lazy, get_read_connection

logger = logging.getLogger("superlocalmemory.routes.lifecycle")
router = APIRouter()

# Feature detection
LIFECYCLE_AVAILABLE = False
try:
    from superlocalmemory.learning.lifecycle_policy import LifecycleManager

    LIFECYCLE_AVAILABLE = True
except ImportError:
    logger.info("V3 lifecycle engine not available")


_STATUS_TTL_S = 20.0
_AGE_STATES = ("active", "warm", "cold", "archive", "forgotten")


@router.get("/api/lifecycle/status")
def lifecycle_status():
    """Get lifecycle state distribution for active profile.

    The counts scan every fact, so a pane that asks again within a few seconds
    gets the last answer instead of a second scan.
    """
    if not LIFECYCLE_AVAILABLE:
        return {"available": False, "message": "Lifecycle engine not available"}

    profile = get_active_profile()
    return cached(("lifecycle-status", str(DB_PATH), profile), _STATUS_TTL_S,
                  lambda: _lifecycle_status(profile),
                  keep=lambda answer: bool(answer.get("available")))


def _lifecycle_status(profile: str) -> dict:
    try:
        conn = get_read_connection(DB_PATH)

        # V3.3: Use fact_retention.lifecycle_zone (Ebbinghaus-driven, authoritative)
        # Falls back to atomic_facts.lifecycle for pre-3.3 databases
        states = {}
        try:
            rows = conn.execute(
                "SELECT lifecycle_zone, COUNT(*) as cnt "
                "FROM fact_retention WHERE profile_id = ? GROUP BY lifecycle_zone",
                (profile,),
            ).fetchall()
            if rows:
                states = {row["lifecycle_zone"]: row["cnt"] for row in rows}
        except sqlite3.OperationalError:
            pass

        if not states:
            # Fallback: V3.0-3.2 schema (atomic_facts.lifecycle column)
            try:
                rows = conn.execute(
                    "SELECT lifecycle, COUNT(*) as cnt "
                    "FROM atomic_facts WHERE profile_id = ? GROUP BY lifecycle",
                    (profile,),
                ).fetchall()
                states = {(row["lifecycle"] or "active"): row["cnt"] for row in rows}
            except sqlite3.OperationalError:
                # V2 fallback: memories table
                try:
                    rows = conn.execute(
                        "SELECT lifecycle, COUNT(*) as cnt "
                        "FROM memories WHERE profile = ? GROUP BY lifecycle",
                        (profile,),
                    ).fetchall()
                    states = {(row["lifecycle"] or "active"): row["cnt"] for row in rows}
                except sqlite3.OperationalError:
                    # No lifecycle column at all — count everything as active
                    total = conn.execute(
                        "SELECT COUNT(*) FROM atomic_facts WHERE profile_id = ?",
                        (profile,),
                    ).fetchone()[0]
                states = {"active": total}

        total = sum(states.values())

        # Age distribution per state (V3.3: join fact_retention with atomic_facts),
        # one pass over the facts for all states rather than one pass per state.
        age_stats = {}
        try:
            rows = conn.execute(
                "SELECT fr.lifecycle_zone AS zone, "
                "AVG(julianday('now') - julianday(af.created_at)) as avg_age, "
                "MIN(julianday('now') - julianday(af.created_at)) as min_age, "
                "MAX(julianday('now') - julianday(af.created_at)) as max_age "
                "FROM fact_retention fr "
                "JOIN atomic_facts af ON fr.fact_id = af.fact_id "
                "WHERE fr.profile_id = ? GROUP BY fr.lifecycle_zone",
                (profile,),
            ).fetchall()
            for row in rows:
                if row["zone"] in _AGE_STATES and row["avg_age"] is not None:
                    age_stats[row["zone"]] = {
                        "avg_days": round(row["avg_age"], 1),
                        "min_days": round(row["min_age"], 1),
                        "max_days": round(row["max_age"], 1),
                    }
            age_stats = {z: age_stats[z] for z in _AGE_STATES if z in age_stats}
        except sqlite3.OperationalError:
            pass

        conn.close()

        return {
            "available": True,
            "active_profile": profile,
            "total_memories": total,
            "states": states,
            "recent_transitions": [],
            "age_stats": age_stats,
        }
    except Exception:
        logger.exception("lifecycle_status error")
        return {"available": False, "error": "Internal server error"}


def _compaction_candidates(mgr, facts):
    candidates = []
    for f in facts:
        new_state = mgr.get_lifecycle_state(f)
        if new_state != f.lifecycle:
            candidates.append(
                {
                    "fact_id": f.fact_id,
                    "current": getattr(f.lifecycle, "value", str(f.lifecycle)),
                    "proposed": getattr(new_state, "value", str(new_state)),
                }
            )
    return candidates


@router.post("/api/lifecycle/compact")
async def trigger_compaction(request: Request, data: dict = {}):
    """Preview or apply profile-scoped lifecycle transitions with shared protections."""
    if not LIFECYCLE_AVAILABLE:
        return {"success": False, "error": "Lifecycle engine not available"}

    dry_run = bool((data or {}).get("dry_run", True))
    try:
        engine = get_engine_lazy(request.app.state)
        if engine is None:
            return {"success": False, "error": "Engine not initialized"}
        profile = get_active_profile()

        mgr = LifecycleManager(engine._db)
        facts = engine._db.get_all_facts(profile)
        candidates = _compaction_candidates(mgr, facts)

        applied = 0
        if not dry_run and candidates:
            authorization = authorize_route_mutation(
                request,
                operation="update",
                source_agent_id="http-lifecycle-compact",
                profile_id=profile,
            )
            from superlocalmemory.storage.retention_lifecycle import compact_facts

            candidates = compact_facts(engine._db, candidates, profile)
            applied = len(candidates)
            authorization.complete()

        return {
            "success": True,
            "dry_run": dry_run,
            "active_profile": profile,
            "total_facts": len(facts),
            "candidates": len(candidates),
            "applied": applied,
            "transitions": candidates[:20],
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("lifecycle compaction failed")
        return {"success": False, "error": "internal error"}
