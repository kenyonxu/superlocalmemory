# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Evolution Store — SQLite persistence for skill evolution history.

Stores evolution records, lineage DAG, and anti-loop state.
Uses the same memory.db as the rest of SLM — no separate database.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from superlocalmemory.evolution.types import (
    EvolutionCandidate,
    EvolutionRecord,
    EvolutionStatus,
    EvolutionType,
    TriggerType,
)

logger = logging.getLogger(__name__)

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS skill_evolution_log (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    parent_skill_id TEXT,
    evolution_type TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    generation INTEGER DEFAULT 0,
    status TEXT DEFAULT 'candidate',
    mutation_summary TEXT DEFAULT '',
    evidence TEXT DEFAULT '[]',
    original_content TEXT DEFAULT '',
    evolved_content TEXT DEFAULT '',
    content_diff TEXT DEFAULT '',
    blind_verified INTEGER DEFAULT 0,
    rejection_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_evo_skill ON skill_evolution_log(skill_name);
CREATE INDEX IF NOT EXISTS idx_evo_status ON skill_evolution_log(status);
CREATE INDEX IF NOT EXISTS idx_evo_created ON skill_evolution_log(created_at);

CREATE TABLE IF NOT EXISTS evolution_cycle_state (
    key TEXT PRIMARY KEY,
    value INTEGER DEFAULT 0,
    updated_at TEXT
);
"""

# Anti-loop budget
MAX_EVOLUTIONS_PER_CYCLE = 3
MAX_ATTEMPTS_PER_SKILL = 3
MIN_FRESH_INVOCATIONS = 5


class EvolutionStore:
    """SQLite persistence for evolution history and anti-loop state."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._ensure_schema()
        self._addressed_degradations: dict[str, set[str]] = {}

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.executescript(_SCHEMA_DDL)
            conn.commit()
        except sqlite3.OperationalError as exc:
            logger.warning("Evolution schema creation failed: %s", exc)
        finally:
            conn.close()

    def reset_cycle(self) -> None:
        """Reset per-cycle counters. Call at start of each consolidation."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO evolution_cycle_state (key, value, updated_at) "
                "VALUES ('cycle_count', 0, ?)",
                (now,),
            )
            conn.commit()
        finally:
            conn.close()

    def can_evolve(self) -> bool:
        """Check if budget allows another evolution this cycle."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT value FROM evolution_cycle_state WHERE key = 'cycle_count'",
            ).fetchone()
            count = row[0] if row else 0
            return count < MAX_EVOLUTIONS_PER_CYCLE
        finally:
            conn.close()

    def record_evolution_attempt(self) -> None:
        """Increment cycle counter in DB."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT value FROM evolution_cycle_state WHERE key = 'cycle_count'",
            ).fetchone()
            current = row[0] if row else 0
            conn.execute(
                "INSERT OR REPLACE INTO evolution_cycle_state (key, value, updated_at) "
                "VALUES ('cycle_count', ?, ?)",
                (current + 1, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_cycle_count(self) -> int:
        """Read current cycle count from DB."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT value FROM evolution_cycle_state WHERE key = 'cycle_count'",
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Anti-loop: addressed degradations (adopted from OpenSpace)
    # ------------------------------------------------------------------

    def is_addressed(self, skill_name: str, context_hash: str) -> bool:
        return context_hash in self._addressed_degradations.get(skill_name, set())

    def mark_addressed(self, skill_name: str, context_hash: str) -> None:
        self._addressed_degradations.setdefault(skill_name, set()).add(context_hash)

    def prune_recovered(self, active_degraded_skills: set[str]) -> None:
        """Remove tracking for skills that recovered."""
        recovered = [
            k for k in self._addressed_degradations
            if k not in active_degraded_skills
        ]
        for k in recovered:
            del self._addressed_degradations[k]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save_record(self, record: EvolutionRecord) -> None:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO skill_evolution_log "
                "(id, skill_name, parent_skill_id, evolution_type, trigger_type, "
                " generation, status, mutation_summary, evidence, "
                " original_content, evolved_content, content_diff, "
                " blind_verified, rejection_reason, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.skill_name,
                    record.parent_skill_id,
                    record.evolution_type.value,
                    record.trigger.value,
                    record.generation,
                    record.status.value,
                    record.mutation_summary,
                    json.dumps(list(record.evidence)),
                    record.original_content,
                    record.evolved_content,
                    record.content_diff,
                    1 if record.blind_verified else 0,
                    record.rejection_reason,
                    record.created_at,
                    record.completed_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_record(self, record_id: str) -> Optional[EvolutionRecord]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM skill_evolution_log WHERE id = ?",
                (record_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_record(dict(row))
        finally:
            conn.close()

    def get_skill_history(self, skill_name: str, limit: int = 20) -> list[EvolutionRecord]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM skill_evolution_log "
                "WHERE skill_name = ? ORDER BY created_at DESC LIMIT ?",
                (skill_name, limit),
            ).fetchall()
            return [self._row_to_record(dict(r)) for r in rows]
        finally:
            conn.close()

    def get_recent(self, limit: int = 10) -> list[EvolutionRecord]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM skill_evolution_log "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_record(dict(r)) for r in rows]
        finally:
            conn.close()

    def count_attempts(self, skill_name: str) -> int:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM skill_evolution_log "
                "WHERE skill_name = ? AND status NOT IN ('promoted')",
                (skill_name,),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def has_exceeded_attempts(self, skill_name: str) -> bool:
        return self.count_attempts(skill_name) >= MAX_ATTEMPTS_PER_SKILL

    def get_stats(self) -> dict:
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM skill_evolution_log",
            ).fetchone()[0]
            by_status = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM skill_evolution_log GROUP BY status",
            ).fetchall():
                by_status[row[0]] = row[1]
            by_type = {}
            for row in conn.execute(
                "SELECT evolution_type, COUNT(*) FROM skill_evolution_log GROUP BY evolution_type",
            ).fetchall():
                by_type[row[0]] = row[1]
            return {
                "total": total,
                "by_status": by_status,
                "by_type": by_type,
                "cycle_budget_remaining": MAX_EVOLUTIONS_PER_CYCLE - self._get_cycle_count(),
            }
        finally:
            conn.close()

    def _row_to_record(self, row: dict) -> EvolutionRecord:
        evidence_raw = row.get("evidence", "[]")
        try:
            evidence = tuple(json.loads(evidence_raw))
        except (json.JSONDecodeError, TypeError):
            evidence = ()

        return EvolutionRecord(
            id=row["id"],
            skill_name=row["skill_name"],
            parent_skill_id=row.get("parent_skill_id"),
            evolution_type=EvolutionType(row["evolution_type"]),
            trigger=TriggerType(row["trigger_type"]),
            generation=row.get("generation", 0),
            status=EvolutionStatus(row.get("status", "candidate")),
            mutation_summary=row.get("mutation_summary", ""),
            evidence=evidence,
            original_content=row.get("original_content", ""),
            evolved_content=row.get("evolved_content", ""),
            content_diff=row.get("content_diff", ""),
            blind_verified=bool(row.get("blind_verified", 0)),
            rejection_reason=row.get("rejection_reason", ""),
            created_at=row.get("created_at", ""),
            completed_at=row.get("completed_at"),
        )
