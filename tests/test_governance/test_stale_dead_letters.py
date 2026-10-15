# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A save that later finished no longer counts as a failed save.

After an upgrade ``slm doctor`` raised a [FAIL] for dead-letter rows whose
operation had since completed: the row was a record of an old failure that was
never removed. Completion now removes it, the readers ignore any that remain,
and ``slm ops repair-saves`` clears them. The second spelling of the
"fact profile mismatch" error is planned like the first.
"""

# ruff: noqa: F811 (pytest fixtures are imported and redeclared as parameters)

from __future__ import annotations

import json
import sqlite3
from argparse import Namespace

from superlocalmemory.cli import doctor_ops, ops_repair_cmd
from superlocalmemory.core.ingestion_command import IngestionState
from superlocalmemory.core.ops_inspection import get_failure_counts, list_failed_operations
from superlocalmemory.storage import save_repair
from superlocalmemory.storage.save_repair_apply import apply
from tests.test_governance.test_saves_refiled_after_scope_drift import drifted  # noqa: F401


def _dead(engine) -> int:
    return engine._db.execute("SELECT COUNT(*) AS n FROM dead_letter_operations")[0]["n"]


def _complete(engine, operation_id: str) -> None:
    engine._db.execute("UPDATE ingestion_operations SET state='complete' WHERE operation_id=?",
                       (operation_id,))


def _plan(engine):
    with sqlite3.connect(engine._config.db_path) as conn:
        return save_repair.plan(conn)


def test_a_completed_operation_is_not_a_failed_save(drifted):
    engine, _, ops, _ = drifted
    assert get_failure_counts(engine._config.db_path)["dead_letter_count"] == 4
    _complete(engine, ops["scope"].operation_id)
    assert _dead(engine) == 4  # the stale row is still on disk
    assert get_failure_counts(engine._config.db_path)["dead_letter_count"] == 3


def test_ops_list_skips_dead_letters_of_completed_operations(drifted):
    engine, _, ops, _ = drifted
    _complete(engine, ops["scope"].operation_id)
    listed = list_failed_operations(engine._config.db_path)
    ids = {row["operation_id"] for row in listed["dead_letter"]}
    assert ops["scope"].operation_id not in ids and len(ids) == 3


def test_doctor_counts_and_names_reasons_without_the_stale_rows(drifted):
    engine, _, ops, _ = drifted
    for name in ("scope", "content", "missing"):
        _complete(engine, ops[name].operation_id)
    status, detail, _ = doctor_ops.failed_saves_finding(engine._config.db_path)
    assert status == "WARN" and detail.startswith("1 save failed")
    assert "1 queryable ingestion memory content mismatch" in detail
    assert "scope mismatch" not in detail
    _complete(engine, ops["edited"].operation_id)
    assert doctor_ops.failed_saves_finding(engine._config.db_path)[0] == "PASS"


def test_finishing_an_operation_removes_its_dead_letter_rows(drifted):
    engine, command, ops, _ = drifted
    op_id = ops["scope"].operation_id
    engine._db.execute(
        "UPDATE ingestion_operations SET state='enriching', lease_owner='me', "
        "lease_expires_at=9999999999 WHERE operation_id=?", (op_id,))
    command.repository.finish_enriching(op_id, owner="me", target=IngestionState.COMPLETE)
    left = engine._db.execute(
        "SELECT COUNT(*) AS n FROM dead_letter_operations WHERE original_op_id=?", (op_id,))
    assert left[0]["n"] == 0 and _dead(engine) == 3


def test_failing_an_operation_keeps_its_dead_letter_rows(drifted):
    engine, command, ops, _ = drifted
    op_id = ops["scope"].operation_id
    engine._db.execute(
        "UPDATE ingestion_operations SET state='enriching', attempt_count=1, lease_owner='me', "
        "lease_expires_at=9999999999 WHERE operation_id=?", (op_id,))
    command.repository.finish_enriching(op_id, owner="me", target=IngestionState.FAILED,
                                        last_error="again")
    assert _dead(engine) == 4


def test_repair_preview_reports_the_stale_rows_and_writes_nothing(drifted):
    engine, _, ops, _ = drifted
    _complete(engine, ops["scope"].operation_id)
    _complete(engine, ops["edited"].operation_id)
    assert _plan(engine).summary()["stale_dead_letters"] == 2
    assert _dead(engine) == 4


def test_repair_apply_clears_them_and_says_how_many(drifted):
    engine, _, ops, _ = drifted
    _complete(engine, ops["edited"].operation_id)  # not refilable, still stale
    result = apply(engine._config.db_path, _plan(engine))
    assert result.stale_dead_letters_cleared == 1
    left = engine._db.execute(
        "SELECT COUNT(*) AS n FROM dead_letter_operations WHERE original_op_id=?",
        (ops["edited"].operation_id,))
    assert left[0]["n"] == 0
    assert _plan(engine).summary()["stale_dead_letters"] == 0


def test_cli_reports_the_stale_count_before_and_after(drifted, capsys, monkeypatch):
    engine, _, ops, _ = drifted
    monkeypatch.setattr(ops_repair_cmd, "_db_path", lambda: engine._config.db_path)
    _complete(engine, ops["edited"].operation_id)
    ops_repair_cmd.cmd_ops_repair_saves(Namespace(apply=False, json=True))
    assert json.loads(capsys.readouterr().out)["stale_dead_letters"] == 1
    ops_repair_cmd.cmd_ops_repair_saves(Namespace(apply=False, json=False))
    assert "saves that later completed: 1" in capsys.readouterr().out
    ops_repair_cmd.cmd_ops_repair_saves(Namespace(apply=True, json=True))
    assert json.loads(capsys.readouterr().out)["stale_dead_letters_cleared"] == 1


def test_the_other_spelling_of_the_missing_fact_error_is_planned(drifted):
    engine, _, ops, _ = drifted
    spelling = "queryable fact profile mismatch or missing fact"
    assert spelling in save_repair.REASONS
    op_id = ops["missing"].operation_id
    engine._db.execute("UPDATE ingestion_operations SET last_error=? WHERE operation_id=?",
                       (spelling, op_id))
    engine._db.execute("UPDATE dead_letter_operations SET error=? WHERE original_op_id=?",
                       (spelling, op_id))
    actions = {item.operation_id: item.action for item in _plan(engine).refile}
    assert actions[op_id] == "add_memory_and_fact"
