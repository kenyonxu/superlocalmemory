"""Focused contract tests for the transport-neutral BrainTruth v1 reader."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from superlocalmemory.brain.truth import BRAIN_TRUTH_V1, BrainTruthService


def _service(tmp_path: Path) -> BrainTruthService:
    return BrainTruthService(
        memory_db_path=tmp_path / "memory.db",
        learning_db_path=tmp_path / "learning.db",
    )


def _create_memory_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE atomic_facts (
                fact_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE correction_cases (
                case_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )


def _create_learning_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE learning_signals (
                id INTEGER PRIMARY KEY,
                profile_id TEXT NOT NULL,
                signal_type TEXT NOT NULL
            );
            CREATE TABLE agent_experiences (
                profile_id TEXT NOT NULL,
                experience_id TEXT NOT NULL,
                verification_authority TEXT NOT NULL
            );
            CREATE TABLE cognitive_turn_receipts (
                profile_id TEXT NOT NULL,
                receipt_id TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE external_evidence_receipts (
                profile_id TEXT NOT NULL,
                run_ref TEXT NOT NULL,
                run_state TEXT NOT NULL,
                demonstration INTEGER NOT NULL,
                eligible_for_learning INTEGER NOT NULL
            );
            """
        )


def test_snapshot_has_one_versioned_transport_neutral_truth_shape(tmp_path: Path) -> None:
    memory, learning = tmp_path / "memory.db", tmp_path / "learning.db"
    _create_memory_db(memory)
    _create_learning_db(learning)
    with sqlite3.connect(memory) as conn:
        conn.executemany(
            "INSERT INTO atomic_facts VALUES (?, ?, ?, ?)",
            [
                ("f1", "alpha", "active", "2099-01-01 00:00:00"),
                ("f2", "alpha", "archived", "2000-01-01 00:00:00"),
                ("f3", "beta", "active", "2099-01-01 00:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO correction_cases VALUES (?, ?, ?)",
            [("c1", "alpha", "proposed"), ("c2", "alpha", "applied")],
        )
    with sqlite3.connect(learning) as conn:
        conn.executemany(
            "INSERT INTO learning_signals (profile_id, signal_type) VALUES (?, ?)",
            [
                ("alpha", "user_correction"),
                ("alpha", "recall_hit"),
                ("beta", "user_positive"),
            ],
        )
        conn.execute("INSERT INTO agent_experiences VALUES ('alpha', 'e1', 'human_approval')")
        conn.executemany(
            "INSERT INTO cognitive_turn_receipts VALUES (?, ?, ?)",
            [("alpha", "t1", "open"), ("alpha", "t2", "finalized")],
        )
        conn.executemany(
            "INSERT INTO external_evidence_receipts VALUES (?, ?, ?, ?, ?)",
            [("alpha", "r1", "SUCCEEDED", 0, 0), ("alpha", "r2", "FAILED", 1, 0)],
        )

    truth = _service(tmp_path).snapshot("alpha")

    assert truth["contract"] == BRAIN_TRUTH_V1
    assert truth["profile_id"] == "alpha"
    assert truth["control_plane"] == "observation_only"
    assert truth["memory_activity"] == {
        "availability": "available",
        "source": "memory.db:atomic_facts",
        "facts_total": 2,
        "facts_by_lifecycle": {"active": 1, "archived": 1},
        "facts_created_last_24h": 1,
    }
    assert truth["feedback"] == {
        "availability": "available",
        "source": "learning.db:learning_signals",
        "signals_total": 2,
        "signals_by_type": {"recall_hit": 1, "user_correction": 1},
        "explicit_signals": 1,
        "implicit_signals": 1,
    }
    experience = truth["agent_experience"]
    assert experience["claimed_experiences_total"] == 1
    assert experience["independently_verified_experiences_total"] == 0
    assert experience["verification_availability"] == "not_supported_by_read_model"
    assert experience["cognitive_turns_by_state"] == {"finalized": 1, "open": 1}
    evidence = truth["external_evidence"]
    assert evidence["receipts_by_run_state"] == {"FAILED": 1, "SUCCEEDED": 1}
    assert evidence["demonstrations_total"] == 1
    assert evidence["eligible_for_learning_total"] == 0
    assert evidence["control_plane"] == "observation_only"
    corrections = truth["correction_quality"]
    assert corrections["cases_by_status"] == {"applied": 1, "proposed": 1}
    assert corrections["review_policy"] == {
        "availability": "not_configured",
        "automatic_application": False,
        "reason": "host-authorized review policy is not attached",
    }


def test_unavailable_store_is_never_reported_as_real_zero(tmp_path: Path) -> None:
    _create_memory_db(tmp_path / "memory.db")

    truth = _service(tmp_path).snapshot("alpha")

    assert truth["memory_activity"]["availability"] == "available"
    assert truth["memory_activity"]["facts_total"] == 0
    assert truth["feedback"] == {
        "availability": "unavailable",
        "source": "learning.db",
        "reason": "missing",
        "signals_total": None,
        "signals_by_type": None,
        "explicit_signals": None,
        "implicit_signals": None,
    }
    assert truth["agent_experience"]["claimed_experiences_total"] is None
    assert truth["external_evidence"]["receipts_total"] is None


def test_missing_m042_ledger_is_unavailable_not_zero_cases(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute(
            "CREATE TABLE atomic_facts "
            "(fact_id TEXT, profile_id TEXT, lifecycle TEXT, created_at TEXT)"
        )
    _create_learning_db(tmp_path / "learning.db")

    corrections = _service(tmp_path).snapshot("alpha")["correction_quality"]

    assert corrections["availability"] == "unavailable"
    assert corrections["reason"] == "schema_unavailable"
    assert corrections["cases_total"] is None
    assert corrections["review_policy"]["availability"] == "unavailable"


def test_learning_sections_fail_independently_when_a_receipt_table_is_absent(
    tmp_path: Path,
) -> None:
    _create_memory_db(tmp_path / "memory.db")
    with sqlite3.connect(tmp_path / "learning.db") as conn:
        conn.execute("CREATE TABLE learning_signals (profile_id TEXT, signal_type TEXT)")

    truth = _service(tmp_path).snapshot("alpha")

    assert truth["feedback"]["availability"] == "available"
    assert truth["feedback"]["signals_total"] == 0
    assert truth["agent_experience"]["availability"] == "unavailable"
    assert truth["agent_experience"]["claimed_experiences_total"] is None
    assert truth["external_evidence"]["availability"] == "unavailable"
    assert truth["external_evidence"]["receipts_total"] is None


def test_snapshot_rejects_empty_profile_identifier(tmp_path: Path) -> None:
    service = _service(tmp_path)

    try:
        service.snapshot("")
    except ValueError as exc:
        assert str(exc) == "profile_id must be a non-empty string"
    else:  # pragma: no cover - makes the boundary explicit without pytest dependency
        raise AssertionError("empty profile_id must be rejected")
