#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Dashboard API routes for compliance engine (ABAC + audit + retention)."""

import sys
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Query

logger = logging.getLogger("superlocalmemory.routes.compliance")
router = APIRouter()

MEMORY_DIR = Path.home() / ".claude-memory"
MEMORY_DB = MEMORY_DIR / "memory.db"
AUDIT_DB = MEMORY_DIR / "audit.db"
ABAC_POLICY_PATH = MEMORY_DIR / "policies" / "abac.json"

# Feature detection — try installed location, then repo src/
COMPLIANCE_AVAILABLE = False
try:
    if str(MEMORY_DIR) not in sys.path:
        sys.path.insert(0, str(MEMORY_DIR))
    from compliance.audit_db import AuditDB
    from compliance.retention_manager import ComplianceRetentionManager
    from compliance.abac_engine import ABACEngine
    COMPLIANCE_AVAILABLE = True
except ImportError:
    try:
        REPO_SRC = Path(__file__).parent.parent / "src"
        if str(REPO_SRC) not in sys.path:
            sys.path.insert(0, str(REPO_SRC))
        from compliance.audit_db import AuditDB
        from compliance.retention_manager import ComplianceRetentionManager
        from compliance.abac_engine import ABACEngine
        COMPLIANCE_AVAILABLE = True
    except ImportError:
        logger.info("Compliance engine not available (missing dependencies)")


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


@router.get("/api/compliance/status")
async def compliance_status():
    """Get compliance engine status for active profile."""
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "message": "Compliance engine not available"}

    try:
        profile = _get_active_profile()

        # Audit events count + recent events
        audit_events_count = 0
        recent_audit_events = []
        if AUDIT_DB.exists():
            conn = sqlite3.connect(str(AUDIT_DB))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM audit_events"
                ).fetchone()
                audit_events_count = row['cnt'] if row else 0

                rows = conn.execute(
                    "SELECT id, event_type, actor, resource_id, "
                    "details, created_at "
                    "FROM audit_events "
                    "ORDER BY id DESC LIMIT 30"
                ).fetchall()
                for r in rows:
                    details = r['details'] or '{}'
                    if isinstance(details, str):
                        try:
                            details = json.loads(details)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    recent_audit_events.append({
                        "id": r['id'],
                        "event_type": r['event_type'],
                        "actor": r['actor'],
                        "resource_id": r['resource_id'],
                        "details": details,
                        "created_at": r['created_at']
                    })
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()

        # Retention policies
        retention_policies = []
        if AUDIT_DB.exists():
            conn = sqlite3.connect(str(AUDIT_DB))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, name, framework, retention_days, action, "
                    "applies_to, created_at "
                    "FROM compliance_retention_rules ORDER BY id"
                ).fetchall()
                for r in rows:
                    applies_to = r['applies_to'] or '{}'
                    if isinstance(applies_to, str):
                        try:
                            applies_to = json.loads(applies_to)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    retention_policies.append({
                        "id": r['id'],
                        "name": r['name'],
                        "framework": r['framework'],
                        "retention_days": r['retention_days'],
                        "action": r['action'],
                        "applies_to": applies_to,
                        "created_at": r['created_at']
                    })
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()

        # ABAC policies count
        abac_policies_count = 0
        try:
            abac = ABACEngine(
                str(ABAC_POLICY_PATH) if ABAC_POLICY_PATH.exists() else None
            )
            abac_policies_count = len(abac.policies)
        except Exception:
            pass

        return {
            "available": True,
            "active_profile": profile,
            "audit_events_count": audit_events_count,
            "recent_audit_events": recent_audit_events,
            "retention_policies": retention_policies,
            "abac_policies_count": abac_policies_count
        }
    except Exception as e:
        logger.error("compliance_status error: %s", e)
        return {"available": False, "error": str(e)}


@router.get("/api/compliance/audit")
async def query_audit_trail(
    limit: int = Query(default=50, ge=1, le=500),
    event_type: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
):
    """Query audit trail events with optional filters.

    Query params:
        limit: max events to return (default 50, max 500)
        event_type: filter by event type (e.g. "retention.rule_created")
        since: ISO timestamp — only return events after this time
    """
    if not COMPLIANCE_AVAILABLE:
        return {"available": False, "error": "Compliance engine not available"}

    if not AUDIT_DB.exists():
        return {"available": True, "events": [], "total": 0}

    try:
        conn = sqlite3.connect(str(AUDIT_DB))
        conn.row_factory = sqlite3.Row

        query = (
            "SELECT id, event_type, actor, resource_id, "
            "details, prev_hash, entry_hash, created_at "
            "FROM audit_events WHERE 1=1"
        )
        params = []

        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)

        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        events = []
        for r in rows:
            details = r['details'] or '{}'
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (json.JSONDecodeError, TypeError):
                    pass
            events.append({
                "id": r['id'],
                "event_type": r['event_type'],
                "actor": r['actor'],
                "resource_id": r['resource_id'],
                "details": details,
                "created_at": r['created_at']
            })

        conn.close()

        return {
            "available": True,
            "events": events,
            "total": len(events),
            "filters": {
                "event_type": event_type,
                "since": since,
                "limit": limit
            }
        }
    except Exception as e:
        logger.error("query_audit_trail error: %s", e)
        return {"available": False, "error": str(e)}


@router.post("/api/compliance/retention-policy")
async def create_retention_policy(data: dict):
    """Create a compliance retention policy.

    Body: {
        name: str,
        retention_days: int,
        category: str (maps to framework),
        action: "archive" | "tombstone" | "notify",
        applies_to: dict (optional, e.g. {"tags": [...], "project_name": "..."})
    }
    """
    if not COMPLIANCE_AVAILABLE:
        return {"success": False, "error": "Compliance engine not available"}

    name = data.get('name')
    retention_days = data.get('retention_days')
    framework = data.get('category', 'custom')
    action = data.get('action')
    applies_to = data.get('applies_to', {})

    if not name or not isinstance(name, str):
        return {"success": False, "error": "name is required (string)"}

    if not isinstance(retention_days, int) or retention_days < 1:
        return {"success": False, "error": "retention_days must be a positive integer"}

    valid_actions = ("archive", "tombstone", "notify")
    if action not in valid_actions:
        return {
            "success": False,
            "error": f"action must be one of: {valid_actions}"
        }

    try:
        profile = _get_active_profile()
        manager = ComplianceRetentionManager(
            memory_db_path=str(MEMORY_DB),
            audit_db_path=str(AUDIT_DB),
        )

        rule_id = manager.create_retention_rule(
            name=name,
            framework=framework,
            retention_days=retention_days,
            action=action,
            applies_to=applies_to,
        )

        return {
            "success": True,
            "rule_id": rule_id,
            "active_profile": profile,
            "message": f"Retention policy '{name}' created ({retention_days}d, {action})"
        }
    except Exception as e:
        logger.error("create_retention_policy error: %s", e)
        return {"success": False, "error": str(e)}
