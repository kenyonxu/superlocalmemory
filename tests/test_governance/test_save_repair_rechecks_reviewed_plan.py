# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""L-06: repairs never apply a reviewed plan to subsequently changed evidence."""

# ruff: noqa: F811 (pytest fixtures are imported and redeclared as parameters)

from __future__ import annotations

import pytest

from superlocalmemory.storage.save_repair_apply import apply
from tests.test_governance.test_saves_refiled_after_scope_drift import (
    _plan,
    drifted,  # noqa: F401
)


@pytest.mark.parametrize("change", [
    "content", "scope", "shared_with", "deleted", "cancelled", "retried",
])
def test_a_changed_save_is_skipped_and_its_dead_letter_preserved(drifted, change):
    engine, _, ops, _ = drifted
    reviewed = _plan(engine)
    op_id = ops["scope"].operation_id
    item = next(item for item in reviewed.refile if item.operation_id == op_id)
    db = engine._db
    if change == "deleted":
        db.execute("DELETE FROM memories WHERE memory_id=?", (item.memory_id,))
    elif change == "cancelled":
        db.execute("UPDATE ingestion_operations SET last_error='cancelled', "
                   "next_retry_at=9999999999 "
                   "WHERE operation_id=?", (op_id,))
        db.execute("DELETE FROM dead_letter_operations WHERE original_op_id=?", (op_id,))
    elif change == "retried":
        db.execute("UPDATE ingestion_operations SET attempt_count=11 WHERE operation_id=?",
                   (op_id,))
    else:
        value = {"content": "edited after review", "scope": "shared",
                 "shared_with": '["sales"]'}[change]
        db.execute(f"UPDATE memories SET {change}=? WHERE memory_id=?", (value, item.memory_id))
    before = dict(db.execute("SELECT * FROM ingestion_operations WHERE operation_id=?",
                             (op_id,))[0])
    result = apply(engine._config.db_path, reviewed)
    assert (result.refiled, result.skipped) == (2, 1)
    after = dict(db.execute("SELECT * FROM ingestion_operations WHERE operation_id=?", (op_id,))[0])
    assert after == before
    assert not db.execute("SELECT 1 FROM atomic_facts WHERE memory_id=?", (item.memory_id,))
    if change != "cancelled":
        assert db.execute("SELECT 1 FROM dead_letter_operations WHERE original_op_id=?", (op_id,))


def test_each_apply_keeps_a_distinct_backup_even_in_the_same_second(drifted, monkeypatch):
    engine, *_ = drifted
    monkeypatch.setattr("superlocalmemory.storage.save_repair_apply.time.time", lambda: 1000)
    first = apply(engine._config.db_path, _plan(engine))
    second = apply(engine._config.db_path, _plan(engine))
    assert first.backup != second.backup
