#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Dashboard API routes for memory lifecycle management."""

import sys
import logging
import sqlite3
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger("superlocalmemory.routes.lifecycle")
router = APIRouter()

MEMORY_DIR = Path.home() / ".claude-memory"
MEMORY_DB = MEMORY_DIR / "memory.db"

# Feature detection — try installed location, then repo src/
LIFECYCLE_AVAILABLE = False
try:
    if str(MEMORY_DIR) not in sys.path:
        sys.path.insert(0, str(MEMORY_DIR))
    from lifecycle import LIFECYCLE_AVAILABLE as _avail
    from lifecycle.lifecycle_engine import LifecycleEngine
    from lifecycle.lifecycle_evaluator import LifecycleEvaluator
    LIFECYCLE_AVAILABLE = _avail
except ImportError:
    try:
        REPO_SRC = Path(__file__).parent.parent / "src"
        if str(REPO_SRC) not in sys.path:
            sys.path.insert(0, str(REPO_SRC))
        from lifecycle.lifecycle_engine import LifecycleEngine
        from lifecycle.lifecycle_evaluator import LifecycleEvaluator
        LIFECYCLE_AVAILABLE = True
    except ImportError:
        logger.info("Lifecycle engine not available (missing dependencies)")


def _get_active_profile() -> str:
    """Get the active profile name."""
    try:
        import json
        config_path = MEMORY_DIR / "profiles.json"
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f).get('active_profile', 'default')
    except Exception:
        pass
    return 'default'


@router.get("/api/lifecycle/status")
async def lifecycle_status():
    """Get lifecycle state distribution for active profile."""
    if not LIFECYCLE_AVAILABLE:
        return {"available": False, "message": "Lifecycle engine not available"}

    try:
        import json as _json
        profile = _get_active_profile()
        conn = sqlite3.connect(str(MEMORY_DB))
        conn.row_factory = sqlite3.Row

        # State distribution — profile-scoped
        try:
            rows = conn.execute(
                "SELECT lifecycle_state, COUNT(*) as cnt "
                "FROM memories WHERE profile = ? GROUP BY lifecycle_state",
                (profile,)
            ).fetchall()
            states = {
                (row['lifecycle_state'] or 'active'): row['cnt']
                for row in rows
            }
        except sqlite3.OperationalError:
            # lifecycle_state column doesn't exist yet — all memories are 'active'
            total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE profile = ?",
                (profile,)
            ).fetchone()[0]
            states = {'active': total}

        total = sum(states.values())

        # Recent transitions (from lifecycle_history JSON)
        recent_transitions = []
        try:
            rows = conn.execute(
                "SELECT id, content, lifecycle_state, lifecycle_history "
                "FROM memories "
                "WHERE profile = ? AND lifecycle_history IS NOT NULL "
                "  AND lifecycle_history != '[]' "
                "ORDER BY lifecycle_updated_at DESC LIMIT 20",
                (profile,)
            ).fetchall()
            for row in rows:
                history = _json.loads(row['lifecycle_history'] or '[]')
                if history:
                    last = history[-1] if isinstance(history[-1], dict) else {}
                    recent_transitions.append({
                        'memory_id': row['id'],
                        'content_preview': (row['content'] or '')[:80],
                        'current_state': row['lifecycle_state'] or 'active',
                        'last_transition': last
                    })
        except sqlite3.OperationalError:
            pass

        # Age distribution per state
        age_stats = {}
        try:
            for state in ('active', 'warm', 'cold', 'archived'):
                row = conn.execute(
                    "SELECT AVG(julianday('now') - julianday(created_at)) as avg_age, "
                    "MIN(julianday('now') - julianday(created_at)) as min_age, "
                    "MAX(julianday('now') - julianday(created_at)) as max_age "
                    "FROM memories WHERE profile = ? AND lifecycle_state = ?",
                    (profile, state)
                ).fetchone()
                if row and row['avg_age'] is not None:
                    age_stats[state] = {
                        'avg_days': round(row['avg_age'], 1),
                        'min_days': round(row['min_age'], 1),
                        'max_days': round(row['max_age'], 1)
                    }
        except sqlite3.OperationalError:
            pass

        conn.close()

        return {
            "available": True,
            "active_profile": profile,
            "total_memories": total,
            "states": states,
            "recent_transitions": recent_transitions,
            "age_stats": age_stats
        }
    except Exception as e:
        logger.error("lifecycle_status error: %s", e)
        return {"available": False, "error": str(e)}


@router.post("/api/lifecycle/compact")
async def trigger_compaction(data: dict = {}):
    """Trigger lifecycle compaction. Body: {dry_run: true/false}."""
    if not LIFECYCLE_AVAILABLE:
        return {"success": False, "error": "Lifecycle engine not available"}

    try:
        dry_run = data.get('dry_run', True)
        profile = _get_active_profile()

        evaluator = LifecycleEvaluator(str(MEMORY_DB))
        recommendations = evaluator.evaluate_memories(profile=profile)

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "active_profile": profile,
                "recommendations": len(recommendations),
                "details": [
                    {
                        "memory_id": r.get('memory_id'),
                        "from": r.get('from_state', 'active'),
                        "to": r.get('to_state'),
                        "reason": r.get('reason', '')
                    }
                    for r in recommendations[:50]
                ]
            }

        # Execute transitions
        engine = LifecycleEngine(str(MEMORY_DB))
        transitioned = 0
        for rec in recommendations:
            try:
                result = engine.transition_memory(
                    rec['memory_id'], rec['to_state'], reason=rec.get('reason', '')
                )
                if result.get('success'):
                    transitioned += 1
            except Exception:
                pass

        return {
            "success": True,
            "dry_run": False,
            "active_profile": profile,
            "transitioned": transitioned,
            "total_evaluated": len(recommendations)
        }
    except Exception as e:
        logger.error("compact error: %s", e)
        return {"success": False, "error": str(e)}
