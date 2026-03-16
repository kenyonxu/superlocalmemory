# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
"""SuperLocalMemory V3 - Lifecycle Routes
 - MIT License

Routes: /api/lifecycle/status, /api/lifecycle/compact
Uses V3 compliance.lifecycle.LifecycleManager.
"""
import json
import logging
import sqlite3

from fastapi import APIRouter

from .helpers import get_active_profile, MEMORY_DIR, DB_PATH

logger = logging.getLogger("superlocalmemory.routes.lifecycle")
router = APIRouter()

# Feature detection
LIFECYCLE_AVAILABLE = False
try:
    from superlocalmemory.compliance.lifecycle import LifecycleManager
    LIFECYCLE_AVAILABLE = True
except ImportError:
    logger.info("V3 lifecycle engine not available")


@router.get("/api/lifecycle/status")
async def lifecycle_status():
    """Get lifecycle state distribution for active profile."""
    if not LIFECYCLE_AVAILABLE:
        return {"available": False, "message": "Lifecycle engine not available"}

    try:
        profile = get_active_profile()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Try V3 schema first (atomic_facts with lifecycle_state)
        states = {}
        try:
            rows = conn.execute(
                "SELECT lifecycle_state, COUNT(*) as cnt "
                "FROM atomic_facts WHERE profile_id = ? GROUP BY lifecycle_state",
                (profile,),
            ).fetchall()
            states = {
                (row['lifecycle_state'] or 'active'): row['cnt']
                for row in rows
            }
        except sqlite3.OperationalError:
            # V2 fallback: memories table
            try:
                rows = conn.execute(
                    "SELECT lifecycle_state, COUNT(*) as cnt "
                    "FROM memories WHERE profile = ? GROUP BY lifecycle_state",
                    (profile,),
                ).fetchall()
                states = {
                    (row['lifecycle_state'] or 'active'): row['cnt']
                    for row in rows
                }
            except sqlite3.OperationalError:
                # No lifecycle_state column at all
                total = conn.execute(
                    "SELECT COUNT(*) FROM atomic_facts WHERE profile_id = ?",
                    (profile,),
                ).fetchone()[0]
                states = {'active': total}

        total = sum(states.values())

        # Age distribution per state
        age_stats = {}
        for state in ('active', 'warm', 'cold', 'archived'):
            try:
                row = conn.execute(
                    "SELECT AVG(julianday('now') - julianday(created_at)) as avg_age, "
                    "MIN(julianday('now') - julianday(created_at)) as min_age, "
                    "MAX(julianday('now') - julianday(created_at)) as max_age "
                    "FROM atomic_facts WHERE profile_id = ? AND lifecycle_state = ?",
                    (profile, state),
                ).fetchone()
                if row and row['avg_age'] is not None:
                    age_stats[state] = {
                        'avg_days': round(row['avg_age'], 1),
                        'min_days': round(row['min_age'], 1),
                        'max_days': round(row['max_age'], 1),
                    }
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
    except Exception as e:
        logger.error("lifecycle_status error: %s", e)
        return {"available": False, "error": str(e)}


@router.post("/api/lifecycle/compact")
async def trigger_compaction(data: dict = {}):
    """Trigger lifecycle compaction. Body: {dry_run: true/false}."""
    if not LIFECYCLE_AVAILABLE:
        return {"success": False, "error": "Lifecycle engine not available"}

    return {"status": "not_implemented", "message": "Coming soon"}
