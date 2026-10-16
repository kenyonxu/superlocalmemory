# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""L-06: retain changed facts and prove the repair's SQLite transaction boundary."""

# ruff: noqa: F811 (pytest fixture imported and redeclared as a parameter)

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage import save_repair, save_repair_apply
from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from tests.test_governance.test_saves_refiled_after_scope_drift import (
    _plan,
    drifted,  # noqa: F401
)


def _own_fact(engine, ops, *, scope="personal"):
    op_id = ops["scope"].operation_id
    item = next(item for item in _plan(engine).refile if item.operation_id == op_id)
    content = engine._db.execute("SELECT raw_content FROM ingestion_operations "
                                 "WHERE operation_id=?", (op_id,))[0]["raw_content"]
    return engine._db.store_fact(AtomicFact(
        fact_id="own-repair-fact", memory_id=item.memory_id,
        profile_id="default", content=content, scope=scope,
    ))


@pytest.mark.parametrize("field,value", [
    ("content", "edited fact"), ("scope", "shared"), ("shared_with", '["sales"]'),
])
def test_a_repoint_does_not_adopt_a_fact_changed_after_review(drifted, field, value):
    engine, _, ops, _ = drifted
    fact_id = _own_fact(engine, ops)
    reviewed = _plan(engine)
    op_id = ops["scope"].operation_id
    assert next(item for item in reviewed.refile if item.operation_id == op_id).action == "repoint"
    engine._db.execute(f"UPDATE atomic_facts SET {field}=? WHERE fact_id=?", (value, fact_id))
    result = save_repair_apply.apply(engine._config.db_path, reviewed)
    assert (result.refiled, result.skipped) == (2, 1)
    assert engine._db.execute("SELECT state FROM ingestion_operations WHERE operation_id=?",
                              (op_id,))[0]["state"] == "failed"
    assert engine._db.execute("SELECT 1 FROM dead_letter_operations WHERE original_op_id=?",
                              (op_id,))


def test_an_existing_fact_with_different_visibility_is_left_for_review(drifted):
    engine, _, ops, _ = drifted
    _own_fact(engine, ops, scope="global")
    planned = _plan(engine)
    assert ops["scope"].operation_id in dict(planned.review)
    assert ops["scope"].operation_id not in {item.operation_id for item in planned.refile}


def test_a_generated_memory_id_that_became_occupied_is_not_overwritten(drifted):
    engine, _, ops, _ = drifted
    reviewed = _plan(engine)
    item = next(item for item in reviewed.refile
                if item.operation_id == ops["missing"].operation_id)
    engine._db.store_memory(MemoryRecord(
        memory_id=item.memory_id, profile_id="default", content="unrelated subsequent memory",
    ))
    result = save_repair_apply.apply(engine._config.db_path, reviewed)
    assert (result.refiled, result.skipped) == (2, 1)
    assert engine._db.execute("SELECT content FROM memories WHERE memory_id=?",
                              (item.memory_id,))[0]["content"] == "unrelated subsequent memory"


def test_the_recheck_excludes_an_independent_sqlite_writer_until_commit(drifted, monkeypatch):
    engine, _, ops, _ = drifted
    reviewed = _plan(engine)
    original_plan = save_repair.plan
    attempted = []
    with sqlite3.connect(engine._config.db_path, timeout=0) as writer:
        def recheck(conn):
            fresh = original_plan(conn)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("UPDATE ingestion_operations SET attempt_count=11 "
                               "WHERE operation_id=?", (ops["scope"].operation_id,))
            writer.rollback()
            attempted.append(True)
            return fresh

        monkeypatch.setattr(save_repair, "plan", recheck)
        result = save_repair_apply.apply(engine._config.db_path, reviewed)
        assert attempted == [True] and result.refiled == 3
        writer.execute("UPDATE ingestion_operations SET attempt_count=1 "
                       "WHERE operation_id=?", (ops["scope"].operation_id,))
        writer.commit()
    assert engine._db.execute("SELECT attempt_count FROM ingestion_operations WHERE operation_id=?",
                              (ops["scope"].operation_id,))[0]["attempt_count"] == 1


def _snapshot(db, tables):
    return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables}


def test_failure_after_one_refile_rolls_back_the_whole_repair(drifted, monkeypatch):
    engine, *_ = drifted
    reviewed = _plan(engine)
    tables = ("ingestion_operations", "memories", "atomic_facts", "dead_letter_operations")
    before = _snapshot(engine._db, tables)
    original_refile = save_repair_apply._refile
    calls = []

    def fail_second(db, item):
        calls.append(item.operation_id)
        if len(calls) == 2:
            raise OSError("fixture write failure")
        return original_refile(db, item)

    monkeypatch.setattr(save_repair_apply, "_refile", fail_second)
    with pytest.raises(OSError, match="fixture write failure"):
        save_repair_apply.apply(engine._config.db_path, reviewed)
    assert len(calls) == 2
    after = _snapshot(engine._db, tables)
    assert after == before
