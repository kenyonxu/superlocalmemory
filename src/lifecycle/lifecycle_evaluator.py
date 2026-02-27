# SPDX-License-Identifier: MIT
# Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
"""Lifecycle evaluation rules — determines which memories should transition.

Evaluates memories against configurable thresholds based on:
- Time since last access (staleness)
- Importance score
- Current lifecycle state

Default rules:
    ACTIVE -> WARM:  no access >= 30 days AND importance <= 6
    WARM -> COLD:    no access >= 90 days AND importance <= 4
    COLD -> ARCHIVED: no access >= 180 days (any importance)

Thresholds configurable via lifecycle_config.json.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Set


# Default evaluation thresholds
DEFAULT_EVAL_CONFIG: Dict[str, Dict[str, Any]] = {
    "active_to_warm": {
        "no_access_days": 30,
        "max_importance": 6,
    },
    "warm_to_cold": {
        "no_access_days": 90,
        "max_importance": 4,
    },
    "cold_to_archived": {
        "no_access_days": 180,
    },
}


class LifecycleEvaluator:
    """Evaluates memories for lifecycle state transitions.

    Scans memories and recommends transitions based on staleness and importance.
    Does NOT execute transitions — returns recommendations for the engine or
    scheduler to act on.
    """

    def __init__(
        self, db_path: Optional[str] = None, config_path: Optional[str] = None
    ):
        if db_path is None:
            db_path = str(Path.home() / ".claude-memory" / "memory.db")
        self._db_path = str(db_path)
        self._config_path = config_path

    def _get_connection(self) -> sqlite3.Connection:
        """Get a SQLite connection to memory.db."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_lifecycle_columns(self) -> None:
        """Ensure v2.8 lifecycle columns exist via LifecycleEngine."""
        try:
            from lifecycle.lifecycle_engine import LifecycleEngine
            engine = LifecycleEngine(db_path=self._db_path)
            engine._ensure_columns()
        except Exception:
            pass  # Best effort — don't block evaluation

    def evaluate_memories(
        self,
        profile: Optional[str] = None,
        retention_overrides: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Scan all memories and return recommended transitions.

        Args:
            profile: Filter by profile (None = all profiles)
            retention_overrides: Set of memory IDs to skip (retention-protected)

        Returns:
            List of recommendation dicts with memory_id, from_state, to_state, reason
        """
        config = self._load_config()
        overrides = retention_overrides or set()

        conn = self._get_connection()
        try:
            query = (
                "SELECT id, lifecycle_state, importance, last_accessed, created_at "
                "FROM memories WHERE lifecycle_state IN ('active', 'warm', 'cold')"
            )
            params: list = []
            if profile:
                query += " AND profile = ?"
                params.append(profile)

            try:
                rows = conn.execute(query, params).fetchall()
            except sqlite3.OperationalError as e:
                if "no such column" in str(e):
                    conn.close()
                    self._ensure_lifecycle_columns()
                    conn = self._get_connection()
                    rows = conn.execute(query, params).fetchall()
                else:
                    raise

            recommendations = []
            now = datetime.now()

            for row in rows:
                if row["id"] in overrides:
                    continue
                rec = self._evaluate_row(row, config, now)
                if rec:
                    recommendations.append(rec)

            return recommendations
        finally:
            conn.close()

    def evaluate_single(
        self,
        memory_id: int,
        retention_overrides: Optional[Set[int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a single memory for potential transition.

        Args:
            memory_id: The memory's database ID
            retention_overrides: Set of memory IDs to skip

        Returns:
            Recommendation dict, or None if no transition recommended
        """
        overrides = retention_overrides or set()
        if memory_id in overrides:
            return None

        config = self._load_config()
        conn = self._get_connection()
        try:
            try:
                row = conn.execute(
                    "SELECT id, lifecycle_state, importance, last_accessed, created_at "
                    "FROM memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
            except sqlite3.OperationalError as e:
                if "no such column" in str(e):
                    conn.close()
                    self._ensure_lifecycle_columns()
                    conn = self._get_connection()
                    row = conn.execute(
                        "SELECT id, lifecycle_state, importance, last_accessed, created_at "
                        "FROM memories WHERE id = ?",
                        (memory_id,),
                    ).fetchone()
                else:
                    raise
            if row is None:
                return None
            return self._evaluate_row(row, config, datetime.now())
        finally:
            conn.close()

    def _evaluate_row(
        self, row: sqlite3.Row, config: Dict, now: datetime
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a single memory row against transition rules."""
        state = row["lifecycle_state"] or "active"
        importance = row["importance"] or 5

        # Determine staleness: prefer last_accessed, fall back to created_at
        last_access_str = row["last_accessed"] or row["created_at"]
        if last_access_str:
            try:
                last_access = datetime.fromisoformat(str(last_access_str))
            except (ValueError, TypeError):
                last_access = now  # Unparseable -> treat as recent (safe default)
        else:
            last_access = now

        days_stale = (now - last_access).days

        if state == "active":
            rules = config.get("active_to_warm", {})
            threshold_days = rules.get("no_access_days", 30)
            max_importance = rules.get("max_importance", 6)
            if days_stale >= threshold_days and importance <= max_importance:
                return self._build_recommendation(
                    row["id"], "active", "warm", days_stale, importance
                )
        elif state == "warm":
            rules = config.get("warm_to_cold", {})
            threshold_days = rules.get("no_access_days", 90)
            max_importance = rules.get("max_importance", 4)
            if days_stale >= threshold_days and importance <= max_importance:
                return self._build_recommendation(
                    row["id"], "warm", "cold", days_stale, importance
                )
        elif state == "cold":
            rules = config.get("cold_to_archived", {})
            threshold_days = rules.get("no_access_days", 180)
            if days_stale >= threshold_days:
                return self._build_recommendation(
                    row["id"], "cold", "archived", days_stale, importance
                )

        return None

    def _build_recommendation(
        self,
        memory_id: int,
        from_state: str,
        to_state: str,
        days_stale: int,
        importance: int,
    ) -> Dict[str, Any]:
        """Build a standardized recommendation dict."""
        reason = f"no_access_{days_stale}d"
        if to_state != "archived":
            reason += f"_importance_{importance}"
        return {
            "memory_id": memory_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "days_stale": days_stale,
            "importance": importance,
        }

    def _load_config(self) -> Dict[str, Any]:
        """Load lifecycle evaluation config from JSON. Returns defaults if missing."""
        try:
            if self._config_path:
                config_path = Path(self._config_path)
            else:
                config_path = Path(self._db_path).parent / "lifecycle_config.json"
            if config_path.exists():
                with open(config_path) as f:
                    user_config = json.load(f)
                merged: Dict[str, Any] = {}
                for key in DEFAULT_EVAL_CONFIG:
                    if key in user_config and isinstance(user_config[key], dict):
                        merged[key] = {**DEFAULT_EVAL_CONFIG[key], **user_config[key]}
                    else:
                        merged[key] = dict(DEFAULT_EVAL_CONFIG[key])
                for key in user_config:
                    if key not in merged:
                        merged[key] = user_config[key]
                return merged
        except Exception:
            pass
        return {k: dict(v) for k, v in DEFAULT_EVAL_CONFIG.items()}
