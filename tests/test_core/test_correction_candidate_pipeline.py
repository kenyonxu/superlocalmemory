"""Canonical write-path contract for automatic correction candidates."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from superlocalmemory.core.store_pipeline import _record_correction_candidate
from superlocalmemory.storage.migrations import M042_correction_case_ledger as m042


class _CoordinatorBoundDatabase:
    """Tiny test double: its transaction is owned by the caller, not the helper."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def raw_connection(self):
        yield self._connection


def test_candidate_uses_the_current_transaction_and_carries_no_detector_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _record_correction_candidate(
            _CoordinatorBoundDatabase(conn),
            operation_id="ingest-1",
            profile_id="alpha",
            scope="project",
            predecessor_fact_id="old-release",
            successor_fact_id="new-release",
            reason_code="temporal_contradiction",
            trusted_actor_id="host-actor-1",
        )
        stored = conn.execute(
            "SELECT reason_code, proposed_by_actor_id FROM correction_cases"
        ).fetchone()
        assert stored == ("temporal_contradiction", "host-actor-1")
        assert conn.execute("SELECT COUNT(*) FROM correction_events").fetchone() == (1,)
        conn.execute("ROLLBACK")
    finally:
        conn.close()

    with sqlite3.connect(path) as verify:
        assert verify.execute("SELECT COUNT(*) FROM correction_cases").fetchone() == (0,)
        assert verify.execute("SELECT COUNT(*) FROM correction_events").fetchone() == (0,)


def test_candidate_refuses_to_self_attest_when_trusted_actor_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        m042.apply(conn)

    with sqlite3.connect(path) as conn:
        _record_correction_candidate(
            _CoordinatorBoundDatabase(conn),
            operation_id="ingest-1",
            profile_id="alpha",
            scope="personal",
            predecessor_fact_id="old-release",
            successor_fact_id="new-release",
            reason_code="consolidation_supersede",
            trusted_actor_id="",
        )
        assert conn.execute("SELECT COUNT(*) FROM correction_cases").fetchone() == (0,)
