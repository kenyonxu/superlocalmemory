"""The versioned, read-only Living Brain truth model.

This module is deliberately below every transport surface.  CLI, MCP, HTTP,
and the dashboard can serialize the same dictionary without importing an
engine, attaching databases, or acquiring a writer lock.  Every store is read
in its own short-lived, SQLite read-only connection; one store failing never
turns a failed measurement into a fabricated zero for another store.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRAIN_TRUTH_V1 = "superlocalmemory.brain-truth/v1"

_EXPLICIT_SIGNAL_TYPES = frozenset(
    {"user_positive", "user_negative", "user_correction", "user_pin", "legacy_feedback"}
)


class BrainTruthService:
    """Build one honest, profile-scoped observation snapshot.

    ``memory.db`` and ``learning.db`` are intentionally passed separately.
    This service never uses ``ATTACH``, starts no transaction, and must not be
    used for recall, ranking, routing, correction application, or learning.
    """

    def __init__(self, *, memory_db_path: str | Path, learning_db_path: str | Path) -> None:
        self._memory_db_path = Path(memory_db_path)
        self._learning_db_path = Path(learning_db_path)

    def snapshot(self, profile_id: str) -> dict[str, Any]:
        """Return the stable BrainTruth v1 payload for ``profile_id``.

        The payload intentionally reports unavailable measurements with
        ``None`` values and a reason.  A missing table or locked/corrupt file
        therefore cannot be mistaken for a real count of zero.
        """
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("profile_id must be a non-empty string")

        memory = self._read_memory(profile_id)
        learning = self._read_learning(profile_id)
        return {
            "contract": BRAIN_TRUTH_V1,
            "profile_id": profile_id,
            "generated_at": _utc_now(),
            "control_plane": "observation_only",
            "memory_activity": memory["memory_activity"],
            "feedback": learning["feedback"],
            "agent_experience": learning["agent_experience"],
            "external_evidence": learning["external_evidence"],
            "correction_quality": memory["correction_quality"],
        }

    def _read_memory(self, profile_id: str) -> dict[str, dict[str, Any]]:
        conn, unavailable = _open_read_only(self._memory_db_path, source="memory.db")
        if unavailable is not None:
            return {
                "memory_activity": _unavailable_activity(unavailable),
                "correction_quality": _unavailable_corrections(unavailable),
            }
        assert conn is not None
        try:
            return {
                "memory_activity": _memory_activity(conn, profile_id),
                "correction_quality": _correction_quality(conn, profile_id),
            }
        finally:
            conn.close()

    def _read_learning(self, profile_id: str) -> dict[str, dict[str, Any]]:
        conn, unavailable = _open_read_only(self._learning_db_path, source="learning.db")
        if unavailable is not None:
            return {
                "feedback": _unavailable_feedback(unavailable),
                "agent_experience": _unavailable_agent_experience(unavailable),
                "external_evidence": _unavailable_external_evidence(unavailable),
            }
        assert conn is not None
        try:
            return {
                "feedback": _feedback(conn, profile_id),
                "agent_experience": _agent_experience(conn, profile_id),
                "external_evidence": _external_evidence(conn, profile_id),
            }
        finally:
            conn.close()


def _open_read_only(
    path: Path, *, source: str
) -> tuple[sqlite3.Connection | None, dict[str, str] | None]:
    """Open a single store without creating it or exposing SQLite errors."""
    if not path.exists():
        return None, _unavailable(source, "missing")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn, None
    except sqlite3.Error:
        return None, _unavailable(source, "read_failed")


def _memory_activity(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    required = {"atomic_facts": {"profile_id", "lifecycle", "created_at"}}
    if not _schema_has(conn, required):
        return _unavailable_activity(_unavailable("memory.db:atomic_facts", "schema_unavailable"))
    try:
        rows = conn.execute(
            "SELECT lifecycle, COUNT(*) AS count FROM atomic_facts "
            "WHERE profile_id=? GROUP BY lifecycle ORDER BY lifecycle",
            (profile_id,),
        ).fetchall()
        recent = conn.execute(
            "SELECT COUNT(*) AS count FROM atomic_facts WHERE profile_id=? "
            "AND created_at >= datetime('now', '-1 day')",
            (profile_id,),
        ).fetchone()
    except sqlite3.Error:
        return _unavailable_activity(_unavailable("memory.db:atomic_facts", "read_failed"))
    by_lifecycle = {str(row["lifecycle"]): int(row["count"]) for row in rows}
    return {
        "availability": "available",
        "source": "memory.db:atomic_facts",
        "facts_total": sum(by_lifecycle.values()),
        "facts_by_lifecycle": by_lifecycle,
        "facts_created_last_24h": int(recent["count"]) if recent is not None else 0,
    }


def _feedback(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    required = {"learning_signals": {"profile_id", "signal_type"}}
    if not _schema_has(conn, required):
        return _unavailable_feedback(
            _unavailable("learning.db:learning_signals", "schema_unavailable")
        )
    try:
        rows = conn.execute(
            "SELECT signal_type, COUNT(*) AS count FROM learning_signals "
            "WHERE profile_id=? GROUP BY signal_type ORDER BY signal_type",
            (profile_id,),
        ).fetchall()
    except sqlite3.Error:
        return _unavailable_feedback(_unavailable("learning.db:learning_signals", "read_failed"))
    by_type = {str(row["signal_type"]): int(row["count"]) for row in rows}
    explicit = sum(count for kind, count in by_type.items() if kind in _EXPLICIT_SIGNAL_TYPES)
    implicit = sum(count for kind, count in by_type.items() if kind not in _EXPLICIT_SIGNAL_TYPES)
    return {
        "availability": "available",
        "source": "learning.db:learning_signals",
        "signals_total": sum(by_type.values()),
        "signals_by_type": by_type,
        "explicit_signals": explicit,
        "implicit_signals": implicit,
    }


def _agent_experience(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    required = {
        "agent_experiences": {"profile_id", "verification_authority"},
        "cognitive_turn_receipts": {"profile_id", "state"},
    }
    if not _schema_has(conn, required):
        return _unavailable_agent_experience(
            _unavailable("learning.db:M040_agent_experience_receipts", "schema_unavailable")
        )
    try:
        claimed = conn.execute(
            "SELECT COUNT(*) AS count FROM agent_experiences WHERE profile_id=?", (profile_id,)
        ).fetchone()
        turn_rows = conn.execute(
            "SELECT state, COUNT(*) AS count FROM cognitive_turn_receipts "
            "WHERE profile_id=? GROUP BY state ORDER BY state",
            (profile_id,),
        ).fetchall()
    except sqlite3.Error:
        return _unavailable_agent_experience(
            _unavailable("learning.db:M040_agent_experience_receipts", "read_failed")
        )
    turns_by_state = {str(row["state"]): int(row["count"]) for row in turn_rows}
    return {
        "availability": "available",
        "source": "learning.db:M040_agent_experience_receipts",
        "claimed_experiences_total": int(claimed["count"]) if claimed is not None else 0,
        # M040 validates a declared authority, but this read-only service has
        # no independent verifier.  Calling those claims verified would be a
        # product-quality lie, so this number is deliberately known to be zero.
        "independently_verified_experiences_total": 0,
        "verification_availability": "not_supported_by_read_model",
        "cognitive_turns_total": sum(turns_by_state.values()),
        "cognitive_turns_by_state": turns_by_state,
    }


def _external_evidence(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    required = {
        "external_evidence_receipts": {
            "profile_id",
            "run_state",
            "demonstration",
            "eligible_for_learning",
        }
    }
    if not _schema_has(conn, required):
        return _unavailable_external_evidence(
            _unavailable("learning.db:M041_external_evidence_receipts", "schema_unavailable")
        )
    try:
        rows = conn.execute(
            "SELECT run_state, COUNT(*) AS count FROM external_evidence_receipts "
            "WHERE profile_id=? GROUP BY run_state ORDER BY run_state",
            (profile_id,),
        ).fetchall()
        demo = conn.execute(
            "SELECT COUNT(*) AS count FROM external_evidence_receipts "
            "WHERE profile_id=? AND demonstration=1",
            (profile_id,),
        ).fetchone()
        eligible = conn.execute(
            "SELECT COUNT(*) AS count FROM external_evidence_receipts "
            "WHERE profile_id=? AND eligible_for_learning=1",
            (profile_id,),
        ).fetchone()
    except sqlite3.Error:
        return _unavailable_external_evidence(
            _unavailable("learning.db:M041_external_evidence_receipts", "read_failed")
        )
    by_state = {str(row["run_state"]): int(row["count"]) for row in rows}
    return {
        "availability": "available",
        "source": "learning.db:M041_external_evidence_receipts",
        "receipts_total": sum(by_state.values()),
        "receipts_by_run_state": by_state,
        "demonstrations_total": int(demo["count"]) if demo is not None else 0,
        "eligible_for_learning_total": int(eligible["count"]) if eligible is not None else 0,
        "control_plane": "observation_only",
    }


def _correction_quality(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    required = {"correction_cases": {"profile_id", "status"}}
    if not _schema_has(conn, required):
        return _unavailable_corrections(
            _unavailable("memory.db:M042_correction_case_ledger", "schema_unavailable")
        )
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM correction_cases "
            "WHERE profile_id=? GROUP BY status ORDER BY status",
            (profile_id,),
        ).fetchall()
    except sqlite3.Error:
        return _unavailable_corrections(
            _unavailable("memory.db:M042_correction_case_ledger", "read_failed")
        )
    by_status = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "availability": "available",
        "source": "memory.db:M042_correction_case_ledger",
        "cases_total": sum(by_status.values()),
        "cases_by_status": by_status,
        # M042 is a ledger.  A policy owner must be supplied by a later host
        # integration; this neutral reader cannot manufacture authorization.
        "review_policy": {
            "availability": "not_configured",
            "automatic_application": False,
            "reason": "host-authorized review policy is not attached",
        },
    }


def _schema_has(conn: sqlite3.Connection, required: dict[str, set[str]]) -> bool:
    try:
        for table, columns in required.items():
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
            if not rows or not columns <= {str(row[1]) for row in rows}:
                return False
    except sqlite3.Error:
        return False
    return True


def _unavailable(source: str, reason: str) -> dict[str, str]:
    return {"availability": "unavailable", "source": source, "reason": reason}


def _unavailable_activity(status: dict[str, str]) -> dict[str, Any]:
    return {
        **status,
        "facts_total": None,
        "facts_by_lifecycle": None,
        "facts_created_last_24h": None,
    }


def _unavailable_feedback(status: dict[str, str]) -> dict[str, Any]:
    return {
        **status,
        "signals_total": None,
        "signals_by_type": None,
        "explicit_signals": None,
        "implicit_signals": None,
    }


def _unavailable_agent_experience(status: dict[str, str]) -> dict[str, Any]:
    return {
        **status,
        "claimed_experiences_total": None,
        "independently_verified_experiences_total": None,
        "verification_availability": "unavailable",
        "cognitive_turns_total": None,
        "cognitive_turns_by_state": None,
    }


def _unavailable_external_evidence(status: dict[str, str]) -> dict[str, Any]:
    return {
        **status,
        "receipts_total": None,
        "receipts_by_run_state": None,
        "demonstrations_total": None,
        "eligible_for_learning_total": None,
        "control_plane": "observation_only",
    }


def _unavailable_corrections(status: dict[str, str]) -> dict[str, Any]:
    return {
        **status,
        "cases_total": None,
        "cases_by_status": None,
        "review_policy": {
            "availability": "unavailable",
            "automatic_application": False,
            "reason": "correction ledger is unavailable",
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
