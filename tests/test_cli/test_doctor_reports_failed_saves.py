# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""`slm doctor` says so when saves failed (4.1.25 audit L-05).

On the audited store `slm doctor` read 25 passes and 0 failures while
`slm ops status` said DEGRADED with 60 dead-letter saves: memories whose
enrichment was given up on after repeated attempts. Doctor now reads the same
counts, warns, and prints the exact commands that inspect and retry them.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from superlocalmemory.cli import doctor_ops
from superlocalmemory.core.ops_remediation import get_failure_counts
from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import M031_dead_letter_operations


def _store(root: Path, dead: list[tuple[str, str]] = ()) -> Path:
    mgr = DatabaseManager(root / "memory.db")
    mgr.initialize(real_schema)
    with mgr.raw_connection() as conn:
        M031_dead_letter_operations.apply(conn)
        conn.executemany(
            "INSERT INTO dead_letter_operations (original_op_id, error, attempt_count, profile_id) "
            "VALUES (?, ?, 10, 'default')", list(dead))
    return root / "memory.db"


def _doctor_rows(root: Path, monkeypatch) -> dict[str, dict]:
    from tests.helpers.doctor import run_doctor_report

    monkeypatch.setenv("SLM_DATA_DIR", str(root))
    captured: list[dict] = []
    with patch("superlocalmemory.cli.json_output.json_print",
               side_effect=lambda event, data=None, **kw: captured.append(data or {})):
        run_doctor_report(Namespace(json=True, quick=True))
    assert captured, "doctor did not run"
    return {c["name"]: c for c in captured[-1]["checks"]}


def test_a_store_with_no_failures_passes(tmp_path) -> None:
    status, detail, fix = doctor_ops.failed_saves_finding(_store(tmp_path))
    assert (status, fix) == ("PASS", "")
    assert "no failed saves" in detail


def test_a_few_dead_letter_saves_warn_and_name_why(tmp_path) -> None:
    db = _store(tmp_path, [("op1", "scope mismatch"), ("op2", "scope mismatch"),
                           ("op3", "incomplete embeddings")])
    status, detail, fix = doctor_ops.failed_saves_finding(db)
    assert status == "WARN"
    assert "3 saves" in detail and "2 scope mismatch" in detail
    assert "1 incomplete embeddings" in detail
    assert "entities, graph links or search indexing" in detail


def test_one_dead_letter_save_reads_in_the_singular(tmp_path) -> None:
    _, detail, _ = doctor_ops.failed_saves_finding(_store(tmp_path, [("op1", "boom")]))
    assert detail.startswith("1 save failed")


def test_the_fix_names_the_exact_commands(tmp_path) -> None:
    _, _, fix = doctor_ops.failed_saves_finding(_store(tmp_path, [("op1", "boom")]))
    assert "slm ops list" in fix
    assert "slm ops resolve <id> --action retry" in fix
    assert "slm ops resolve <id> --action cancel" in fix


def test_sixty_dead_letter_saves_fail(tmp_path) -> None:
    db = _store(tmp_path, [(f"op{i}", "scope mismatch") for i in range(60)])
    status, detail, _ = doctor_ops.failed_saves_finding(db)
    assert status == "FAIL" and "60 saves" in detail


def test_the_threshold_is_exactly_fifty(tmp_path) -> None:
    under, at = tmp_path / "under", tmp_path / "at"
    under.mkdir()
    at.mkdir()
    rows = lambda n: [(f"op{i}", "x") for i in range(n)]  # noqa: E731
    assert doctor_ops.failed_saves_finding(_store(under, rows(doctor_ops.FAIL_AT - 1)))[0] == "WARN"
    assert doctor_ops.failed_saves_finding(_store(at, rows(doctor_ops.FAIL_AT)))[0] == "FAIL"


def test_doctor_and_ops_status_read_the_same_counts(tmp_path) -> None:
    db = _store(tmp_path, [(f"op{i}", "x") for i in range(7)])
    assert get_failure_counts(db)["dead_letter_count"] == 7
    assert "7 saves" in doctor_ops.failed_saves_finding(db)[1]


def test_a_store_that_predates_the_table_is_not_a_finding(tmp_path) -> None:
    mgr = DatabaseManager(tmp_path / "memory.db")
    mgr.initialize(real_schema)
    assert doctor_ops.failed_saves_finding(tmp_path / "memory.db")[0] == "PASS"


def test_slm_doctor_itself_warns(tmp_path, monkeypatch) -> None:
    _store(tmp_path, [("op1", "scope mismatch"), ("op2", "enrichment exhausted")])
    row = _doctor_rows(tmp_path, monkeypatch)["Failed saves"]
    assert row["status"] == "WARN"
    assert "2 saves" in row["detail"] and "slm ops list" in row["fix"]


def test_slm_doctor_fails_on_sixty(tmp_path, monkeypatch) -> None:
    _store(tmp_path, [(f"op{i}", "scope mismatch") for i in range(60)])
    rows = _doctor_rows(tmp_path, monkeypatch)
    assert rows["Failed saves"]["status"] == "FAIL"
    assert rows["Database"]["status"] == "PASS"  # the failure is in the saves, not the file


def test_slm_doctor_passes_a_clean_store(tmp_path, monkeypatch) -> None:
    _store(tmp_path)
    assert _doctor_rows(tmp_path, monkeypatch)["Failed saves"]["status"] == "PASS"
