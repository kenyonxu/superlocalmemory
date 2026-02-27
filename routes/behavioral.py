#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Dashboard API routes for behavioral learning engine."""

import sys
import json
import logging
import sqlite3
from pathlib import Path
from fastapi import APIRouter

logger = logging.getLogger("superlocalmemory.routes.behavioral")
router = APIRouter()

MEMORY_DIR = Path.home() / ".claude-memory"
LEARNING_DB = MEMORY_DIR / "learning.db"

# Feature detection — try installed location, then repo src/
BEHAVIORAL_AVAILABLE = False
try:
    if str(MEMORY_DIR) not in sys.path:
        sys.path.insert(0, str(MEMORY_DIR))
    from behavioral.outcome_tracker import OutcomeTracker
    from behavioral.behavioral_patterns import BehavioralPatternExtractor
    from behavioral.cross_project_transfer import CrossProjectTransfer
    BEHAVIORAL_AVAILABLE = True
except ImportError:
    try:
        REPO_SRC = Path(__file__).parent.parent / "src"
        if str(REPO_SRC) not in sys.path:
            sys.path.insert(0, str(REPO_SRC))
        from behavioral.outcome_tracker import OutcomeTracker
        from behavioral.behavioral_patterns import BehavioralPatternExtractor
        from behavioral.cross_project_transfer import CrossProjectTransfer
        BEHAVIORAL_AVAILABLE = True
    except ImportError:
        logger.info("Behavioral engine not available (missing dependencies)")


def _get_active_profile() -> str:
    """Get the active profile name."""
    try:
        config_path = MEMORY_DIR / "profiles.json"
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f).get('active_profile', 'default')
    except Exception:
        pass
    return 'default'


@router.get("/api/behavioral/status")
async def behavioral_status():
    """Get behavioral learning status for active profile."""
    if not BEHAVIORAL_AVAILABLE:
        return {"available": False, "message": "Behavioral engine not available"}

    try:
        profile = _get_active_profile()

        if not LEARNING_DB.exists():
            return {
                "available": True,
                "active_profile": profile,
                "total_outcomes": 0,
                "outcome_breakdown": {"success": 0, "failure": 0, "partial": 0},
                "patterns": [],
                "cross_project_transfers": 0,
                "recent_outcomes": []
            }

        conn = sqlite3.connect(str(LEARNING_DB))
        conn.row_factory = sqlite3.Row

        # Total outcomes for this profile
        total_outcomes = 0
        outcome_breakdown = {"success": 0, "failure": 0, "partial": 0}
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM action_outcomes WHERE profile = ?",
                (profile,)
            ).fetchone()
            total_outcomes = row['cnt'] if row else 0

            rows = conn.execute(
                "SELECT outcome, COUNT(*) as cnt "
                "FROM action_outcomes WHERE profile = ? GROUP BY outcome",
                (profile,)
            ).fetchall()
            for r in rows:
                outcome_key = r['outcome']
                if outcome_key in outcome_breakdown:
                    outcome_breakdown[outcome_key] = r['cnt']
        except sqlite3.OperationalError:
            pass

        # Behavioral patterns for this profile
        patterns = []
        try:
            rows = conn.execute(
                "SELECT pattern_type, pattern_key, success_rate, "
                "evidence_count, confidence "
                "FROM behavioral_patterns "
                "WHERE profile = ? OR project = ? "
                "ORDER BY confidence DESC",
                (profile, profile)
            ).fetchall()
            patterns = [
                {
                    "pattern_type": r['pattern_type'],
                    "pattern_key": r['pattern_key'],
                    "success_rate": round(r['success_rate'], 4),
                    "evidence_count": r['evidence_count'],
                    "confidence": round(r['confidence'], 4)
                }
                for r in rows
            ]
        except sqlite3.OperationalError:
            pass

        # Cross-project transfers involving this profile
        cross_project_transfers = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM cross_project_behaviors "
                "WHERE source_project = ? OR target_project = ?",
                (profile, profile)
            ).fetchone()
            cross_project_transfers = row['cnt'] if row else 0
        except sqlite3.OperationalError:
            pass

        # Recent outcomes (last 20)
        recent_outcomes = []
        try:
            rows = conn.execute(
                "SELECT memory_ids, outcome, action_type, created_at "
                "FROM action_outcomes WHERE profile = ? "
                "ORDER BY created_at DESC LIMIT 20",
                (profile,)
            ).fetchall()
            for r in rows:
                recent_outcomes.append({
                    "memory_ids": json.loads(r['memory_ids'] or '[]'),
                    "outcome": r['outcome'],
                    "action_type": r['action_type'],
                    "created_at": r['created_at']
                })
        except sqlite3.OperationalError:
            pass

        conn.close()

        return {
            "available": True,
            "active_profile": profile,
            "total_outcomes": total_outcomes,
            "outcome_breakdown": outcome_breakdown,
            "patterns": patterns,
            "cross_project_transfers": cross_project_transfers,
            "recent_outcomes": recent_outcomes
        }
    except Exception as e:
        logger.error("behavioral_status error: %s", e)
        return {"available": False, "error": str(e)}


@router.post("/api/behavioral/report-outcome")
async def report_outcome(data: dict):
    """Record an action outcome for behavioral learning.

    Body: {
        memory_ids: [int, ...],
        outcome: "success" | "failure" | "partial",
        action_type: "code_written" | "decision_made" | ... (optional),
        context: "optional note" (optional)
    }
    """
    if not BEHAVIORAL_AVAILABLE:
        return {"success": False, "error": "Behavioral engine not available"}

    memory_ids = data.get('memory_ids')
    outcome = data.get('outcome')
    action_type = data.get('action_type', 'other')
    context_note = data.get('context', '')

    if not memory_ids or not isinstance(memory_ids, list):
        return {"success": False, "error": "memory_ids must be a non-empty list"}

    valid_outcomes = ("success", "failure", "partial")
    if outcome not in valid_outcomes:
        return {
            "success": False,
            "error": f"outcome must be one of: {valid_outcomes}"
        }

    try:
        profile = _get_active_profile()
        tracker = OutcomeTracker(str(LEARNING_DB))

        context_dict = {"note": context_note} if context_note else {}
        row_id = tracker.record_outcome(
            memory_ids=memory_ids,
            outcome=outcome,
            action_type=action_type,
            context=context_dict,
            project=profile,
        )

        if row_id is None:
            return {"success": False, "error": "Failed to record outcome"}

        return {
            "success": True,
            "outcome_id": row_id,
            "active_profile": profile,
            "message": f"Recorded {outcome} outcome for {len(memory_ids)} memories"
        }
    except Exception as e:
        logger.error("report_outcome error: %s", e)
        return {"success": False, "error": str(e)}
