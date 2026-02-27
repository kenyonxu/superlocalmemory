# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""v2.8 MCP tool handlers — lifecycle, behavioral, compliance tools.

These functions implement the 6 new MCP tools added in v2.8.
They are registered with the MCP server in mcp_server.py.
Each function is a thin wrapper around the appropriate engine.

Tool list:
    1. report_outcome        — Record action outcomes for behavioral learning
    2. get_lifecycle_status   — View memory lifecycle states
    3. set_retention_policy   — Configure compliance retention policies
    4. compact_memories       — Trigger lifecycle transitions
    5. get_behavioral_patterns — View learned behavioral patterns
    6. audit_trail            — Query compliance audit trail
"""
import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Default database paths — overridable for testing
DEFAULT_MEMORY_DB = str(Path.home() / ".claude-memory" / "memory.db")
DEFAULT_LEARNING_DB = str(Path.home() / ".claude-memory" / "learning.db")
DEFAULT_AUDIT_DB = str(Path.home() / ".claude-memory" / "audit.db")

# Ensure src/ is on the path so subpackage imports work
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


async def report_outcome(
    memory_ids: list,
    outcome: str,
    action_type: str = "other",
    context: Optional[str] = None,
    agent_id: str = "user",
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an action outcome for behavioral learning.

    Args:
        memory_ids: List of memory IDs involved in the action.
        outcome: One of 'success', 'failure', or 'partial'.
        action_type: Category (code_written, decision_made, debug_resolved, etc.).
        context: Optional JSON string with additional context metadata.
        agent_id: Identifier for the reporting agent.
        project: Project name for scoping outcomes.

    Returns:
        Dict with success status and outcome_id on success.
    """
    try:
        from behavioral.outcome_tracker import OutcomeTracker

        tracker = OutcomeTracker(DEFAULT_LEARNING_DB)
        ctx = json.loads(context) if context else {}
        outcome_id = tracker.record_outcome(
            memory_ids=memory_ids,
            outcome=outcome,
            action_type=action_type,
            context=ctx,
            agent_id=agent_id,
            project=project,
        )
        if outcome_id is None:
            return {
                "success": False,
                "error": f"Invalid outcome: {outcome}. Use success/failure/partial.",
            }
        return {
            "success": True,
            "outcome_id": outcome_id,
            "outcome": outcome,
            "memory_ids": memory_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_lifecycle_status(
    memory_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Get lifecycle status — distribution across states or single memory state.

    Args:
        memory_id: Optional specific memory ID. If None, returns full distribution.

    Returns:
        Dict with state distribution or single memory lifecycle state.
    """
    try:
        from lifecycle.lifecycle_engine import LifecycleEngine

        engine = LifecycleEngine(DEFAULT_MEMORY_DB)
        if memory_id is not None:
            state = engine.get_memory_state(memory_id)
            if state is None:
                return {"success": False, "error": f"Memory {memory_id} not found"}
            return {
                "success": True,
                "memory_id": memory_id,
                "lifecycle_state": state,
            }
        else:
            dist = engine.get_state_distribution()
            total = sum(dist.values())
            return {
                "success": True,
                "distribution": dist,
                "total_memories": total,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def set_retention_policy(
    name: str,
    framework: str,
    retention_days: int,
    action: str = "retain",
    applies_to_tags: Optional[list] = None,
    applies_to_project: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a retention policy for compliance.

    Args:
        name: Policy name (e.g., 'GDPR Erasure', 'HIPAA Retention').
        framework: Regulatory framework (gdpr, hipaa, eu_ai_act, internal).
        retention_days: Days to retain (0 = immediate action).
        action: Policy action (retain, tombstone, archive).
        applies_to_tags: Tags that trigger this policy.
        applies_to_project: Project name that triggers this policy.

    Returns:
        Dict with policy_id on success.
    """
    try:
        from lifecycle.retention_policy import RetentionPolicyManager

        mgr = RetentionPolicyManager(DEFAULT_MEMORY_DB)
        applies_to: Dict[str, Any] = {}
        if applies_to_tags:
            applies_to["tags"] = applies_to_tags
        if applies_to_project:
            applies_to["project_name"] = applies_to_project
        policy_id = mgr.create_policy(
            name=name,
            retention_days=retention_days,
            framework=framework,
            action=action,
            applies_to=applies_to,
        )
        return {
            "success": True,
            "policy_id": policy_id,
            "name": name,
            "framework": framework,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def compact_memories(
    dry_run: bool = True,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate and compact memories — transition stale ones through lifecycle.

    Args:
        dry_run: If True (default), show what would happen without changes.
        profile: Optional profile filter to scope compaction.

    Returns:
        Dict with recommendations (dry_run=True) or transition counts (dry_run=False).
    """
    active_profile = profile
    try:
        from lifecycle.lifecycle_evaluator import LifecycleEvaluator
        from lifecycle.lifecycle_engine import LifecycleEngine

        evaluator = LifecycleEvaluator(DEFAULT_MEMORY_DB)
        recommendations = evaluator.evaluate_memories(profile=active_profile)

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "recommendations": len(recommendations),
                "details": [
                    {
                        "memory_id": r["memory_id"],
                        "from": r["from_state"],
                        "to": r["to_state"],
                        "reason": r["reason"],
                    }
                    for r in recommendations[:20]
                ],
            }

        engine = LifecycleEngine(DEFAULT_MEMORY_DB)
        transitioned = 0
        for rec in recommendations:
            result = engine.transition_memory(
                rec["memory_id"], rec["to_state"], reason=rec["reason"]
            )
            if result.get("success"):
                transitioned += 1

        return {
            "success": True,
            "dry_run": False,
            "evaluated": len(recommendations),
            "transitioned": transitioned,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_behavioral_patterns(
    min_confidence: float = 0.0,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """Get learned behavioral patterns from outcome analysis.

    Args:
        min_confidence: Minimum confidence threshold (0.0-1.0).
        project: Optional project filter.

    Returns:
        Dict with patterns list and count.
    """
    try:
        from behavioral.behavioral_patterns import BehavioralPatternExtractor

        extractor = BehavioralPatternExtractor(DEFAULT_LEARNING_DB)
        patterns = extractor.get_patterns(
            min_confidence=min_confidence, project=project
        )
        return {"success": True, "patterns": patterns, "count": len(patterns)}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def audit_trail(
    event_type: Optional[str] = None,
    actor: Optional[str] = None,
    limit: int = 50,
    verify_chain: bool = False,
) -> Dict[str, Any]:
    """Query the compliance audit trail.

    Args:
        event_type: Filter by event type (memory.created, memory.recalled, etc.).
        actor: Filter by actor (user, agent_id, etc.).
        limit: Max events to return.
        verify_chain: If True, verify hash chain integrity.

    Returns:
        Dict with events list, count, and optional chain verification result.
    """
    try:
        from compliance.audit_db import AuditDB

        db = AuditDB(DEFAULT_AUDIT_DB)
        result: Dict[str, Any] = {"success": True}

        if verify_chain:
            chain_result = db.verify_chain()
            result["chain_valid"] = chain_result["valid"]
            result["chain_entries"] = chain_result["entries_checked"]

        events = db.query_events(
            event_type=event_type, actor=actor, limit=limit
        )
        result["events"] = events
        result["count"] = len(events)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
