# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Memory lifecycle state machine with formal transition rules.

State Machine:
    ACTIVE -> WARM -> COLD -> ARCHIVED -> TOMBSTONED

Reactivation allowed from WARM, COLD, ARCHIVED back to ACTIVE.
TOMBSTONED is terminal (deletion only).

Each transition is recorded in lifecycle_history (JSON array) for auditability.
Thread-safe via threading.Lock() around read-modify-write operations.
"""
import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List


class LifecycleEngine:
    """Manages memory lifecycle states: ACTIVE -> WARM -> COLD -> ARCHIVED -> TOMBSTONED."""

    STATES = ("active", "warm", "cold", "archived", "tombstoned")

    TRANSITIONS = {
        "active": ["warm"],
        "warm": ["active", "cold"],
        "cold": ["active", "archived"],
        "archived": ["active", "tombstoned"],
        "tombstoned": [],  # Terminal state
    }

    def __init__(self, db_path: Optional[str] = None, config_path: Optional[str] = None):
        if db_path is None:
            db_path = Path.home() / ".claude-memory" / "memory.db"
        self._db_path = str(db_path)
        self._config_path = config_path
        self._lock = threading.Lock()
        self._ensure_columns()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a SQLite connection to memory.db."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_columns(self) -> None:
        """Ensure v2.8 lifecycle columns exist in memories table."""
        try:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(memories)")
                existing = {row[1] for row in cursor.fetchall()}
                v28_cols = [
                    ("lifecycle_state", "TEXT DEFAULT 'active'"),
                    ("lifecycle_updated_at", "TIMESTAMP"),
                    ("lifecycle_history", "TEXT DEFAULT '[]'"),
                    ("access_level", "TEXT DEFAULT 'public'"),
                ]
                for col_name, col_type in v28_cols:
                    if col_name not in existing:
                        try:
                            cursor.execute(
                                f"ALTER TABLE memories ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # Graceful degradation — don't block engine init

    def is_valid_transition(self, from_state: str, to_state: str) -> bool:
        """Check if a state transition is valid per the state machine.

        Args:
            from_state: Current lifecycle state
            to_state: Target lifecycle state

        Returns:
            True if the transition is allowed, False otherwise
        """
        if from_state not in self.TRANSITIONS:
            return False
        return to_state in self.TRANSITIONS[from_state]

    def get_memory_state(self, memory_id: int) -> Optional[str]:
        """Get the current lifecycle state of a memory.

        Args:
            memory_id: The memory's database ID

        Returns:
            The lifecycle state string, or None if memory not found
        """
        conn = self._get_connection()
        try:
            try:
                row = conn.execute(
                    "SELECT lifecycle_state FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
            except sqlite3.OperationalError as e:
                if "no such column" in str(e):
                    conn.close()
                    self._ensure_columns()
                    conn = self._get_connection()
                    row = conn.execute(
                        "SELECT lifecycle_state FROM memories WHERE id = ?",
                        (memory_id,),
                    ).fetchone()
                else:
                    raise
            if row is None:
                return None
            return row["lifecycle_state"] or "active"
        finally:
            conn.close()

    def transition_memory(
        self,
        memory_id: int,
        to_state: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Transition a memory to a new lifecycle state.

        Validates the transition against the state machine, updates the database,
        and appends to the lifecycle_history JSON array.

        Args:
            memory_id: The memory's database ID
            to_state: Target lifecycle state
            reason: Human-readable reason for the transition

        Returns:
            Dict with success/failure status, from_state, to_state, etc.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT lifecycle_state, lifecycle_history FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()

                if row is None:
                    return {"success": False, "error": f"Memory {memory_id} not found"}

                from_state = row["lifecycle_state"] or "active"

                if not self.is_valid_transition(from_state, to_state):
                    return {
                        "success": False,
                        "error": f"Invalid transition from '{from_state}' to '{to_state}'",
                    }

                now = datetime.now().isoformat()
                history = json.loads(row["lifecycle_history"] or "[]")
                history.append({
                    "from": from_state,
                    "to": to_state,
                    "reason": reason,
                    "timestamp": now,
                })

                conn.execute(
                    """UPDATE memories
                       SET lifecycle_state = ?,
                           lifecycle_updated_at = ?,
                           lifecycle_history = ?
                       WHERE id = ?""",
                    (to_state, now, json.dumps(history), memory_id),
                )
                conn.commit()

                self._try_emit_event("lifecycle.transitioned", memory_id, {
                    "from_state": from_state,
                    "to_state": to_state,
                    "reason": reason,
                })

                return {
                    "success": True,
                    "from_state": from_state,
                    "to_state": to_state,
                    "memory_id": memory_id,
                    "reason": reason,
                    "timestamp": now,
                }
            finally:
                conn.close()

    def batch_transition(
        self,
        memory_ids: List[int],
        to_state: str,
        reasons: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Transition multiple memories in a single connection + commit.

        Validates each transition individually, skips invalid ones.
        Much faster than calling transition_memory() in a loop because
        it opens only one connection and commits once.

        Args:
            memory_ids: List of memory IDs to transition
            to_state: Target lifecycle state for all
            reasons: Per-memory reasons (defaults to empty string)

        Returns:
            Dict with succeeded (list), failed (list), and counts
        """
        if reasons is None:
            reasons = [""] * len(memory_ids)

        succeeded: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []

        with self._lock:
            conn = self._get_connection()
            try:
                now = datetime.now().isoformat()

                for mem_id, reason in zip(memory_ids, reasons):
                    row = conn.execute(
                        "SELECT lifecycle_state, lifecycle_history "
                        "FROM memories WHERE id = ?",
                        (mem_id,),
                    ).fetchone()

                    if row is None:
                        failed.append({"memory_id": mem_id, "error": "not_found"})
                        continue

                    from_state = row["lifecycle_state"] or "active"
                    if not self.is_valid_transition(from_state, to_state):
                        failed.append({
                            "memory_id": mem_id,
                            "error": f"invalid_{from_state}_to_{to_state}",
                        })
                        continue

                    history = json.loads(row["lifecycle_history"] or "[]")
                    history.append({
                        "from": from_state,
                        "to": to_state,
                        "reason": reason,
                        "timestamp": now,
                    })

                    conn.execute(
                        """UPDATE memories
                           SET lifecycle_state = ?,
                               lifecycle_updated_at = ?,
                               lifecycle_history = ?
                           WHERE id = ?""",
                        (to_state, now, json.dumps(history), mem_id),
                    )
                    succeeded.append({
                        "memory_id": mem_id,
                        "from_state": from_state,
                        "to_state": to_state,
                    })

                conn.commit()

                # Best-effort event emission for each transitioned memory
                for entry in succeeded:
                    self._try_emit_event(
                        "lifecycle.transitioned", entry["memory_id"], {
                            "from_state": entry["from_state"],
                            "to_state": entry["to_state"],
                            "reason": "batch",
                        },
                    )

                return {
                    "succeeded": succeeded,
                    "failed": failed,
                    "total": len(memory_ids),
                    "success_count": len(succeeded),
                    "fail_count": len(failed),
                }
            finally:
                conn.close()

    def reactivate_memory(
        self,
        memory_id: int,
        trigger: str = "",
    ) -> Dict[str, Any]:
        """Reactivate a non-active memory back to ACTIVE state.

        Convenience wrapper around transition_memory for reactivation.
        Valid from WARM, COLD, or ARCHIVED states.

        Args:
            memory_id: The memory's database ID
            trigger: What triggered reactivation (e.g., "recall", "explicit")

        Returns:
            Dict with success/failure status
        """
        return self.transition_memory(
            memory_id, "active", reason=f"reactivated:{trigger}"
        )

    def get_state_distribution(self) -> Dict[str, int]:
        """Get count of memories in each lifecycle state.

        Returns:
            Dict mapping state names to counts (all STATES keys present)
        """
        conn = self._get_connection()
        try:
            dist = {state: 0 for state in self.STATES}
            try:
                rows = conn.execute(
                    "SELECT lifecycle_state, COUNT(*) as cnt "
                    "FROM memories GROUP BY lifecycle_state"
                ).fetchall()
            except sqlite3.OperationalError as e:
                if "no such column" in str(e):
                    conn.close()
                    self._ensure_columns()
                    conn = self._get_connection()
                    rows = conn.execute(
                        "SELECT lifecycle_state, COUNT(*) as cnt "
                        "FROM memories GROUP BY lifecycle_state"
                    ).fetchall()
                else:
                    raise
            for row in rows:
                state = row["lifecycle_state"] if row["lifecycle_state"] else "active"
                if state in dist:
                    dist[state] = row["cnt"]
            return dist
        finally:
            conn.close()

    def _try_emit_event(
        self, event_type: str, memory_id: int, payload: dict
    ) -> None:
        """Best-effort EventBus emission. Fails silently if unavailable."""
        try:
            from event_bus import EventBus
            bus = EventBus.get_instance(Path(self._db_path))
            bus.emit(event_type, payload=payload, memory_id=memory_id)
        except Exception:
            pass
