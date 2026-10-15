# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Failed-operation inspection is honest and scoped (4.1.25 audit OPS-10, OPS-22, OPS-23).

* OPS-10: a store that cannot be read reported "0 failures", the same answer
  as a healthy queue.
* OPS-22: an admin of ``default`` listed ``sales``' failures (with their error
  text) and cancelled one by id; the audit row named ``admin`` and no workspace.
* OPS-23: "retry" with no running engine deleted the dead-letter row and said
  "the daemon will retry", while nothing retried it.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from superlocalmemory.core.ops_inspection import get_failure_counts, list_failed_operations
from superlocalmemory.core.ops_remediation import resolve_operation
from tests.test_governance.test_role_surface_matrix_rest import matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def _seed_dead_letter(db_path, profile, op_id, error="PRIVATE_SALES_ERROR"):
    from superlocalmemory.storage.migrations import M031_dead_letter_operations

    conn = sqlite3.connect(db_path)
    M031_dead_letter_operations.apply(conn)
    conn.execute(
        "INSERT INTO dead_letter_operations (original_op_id, operation_type, content, error, "
        "attempt_count, profile_id, dead_lettered_at) VALUES (?,?,?,?,?,?,?)",
        (op_id, "M018", "c", error, 10, profile, time.time()))
    conn.commit()
    conn.close()


def _dead_letter_rows(db_path, op_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM dead_letter_operations WHERE original_op_id=?",
                            (op_id,)).fetchone()[0]
    finally:
        conn.close()


# -- OPS-10 ------------------------------------------------------------------------


@pytest.mark.parametrize("contents", [b"this is not a database at all" * 50, None])
def test_an_unreadable_store_is_never_zero_failures(tmp_path, contents):
    db = tmp_path / "memory.db"
    if contents is not None:
        db.write_bytes(contents)
    result = list_failed_operations(db)
    assert result["success"] is False
    assert set(result["unavailable"]) == {
        "dead_letter", "degraded_manifests", "exhausted_obligations"}
    counts = get_failure_counts(db)
    assert counts["unavailable"], counts
    assert not db.exists() or contents is not None  # a missing store is not created


def test_a_table_that_was_never_created_is_not_applicable_not_a_failure(tmp_path):
    db = tmp_path / "memory.db"
    sqlite3.connect(db).close()
    result = list_failed_operations(db)
    assert result["success"] is True and result["total"] == 0
    assert {v["status"] for v in result["inspection"].values()} == {"not_applicable"}


def test_a_broken_table_is_unavailable(tmp_path):
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dead_letter_operations (nonsense TEXT)")
    conn.commit()
    conn.close()
    result = list_failed_operations(db)
    assert result["success"] is False
    assert result["inspection"]["dead_letter"]["status"] == "unavailable"


def test_doctor_says_unknown_rather_than_pass(tmp_path):
    from superlocalmemory.cli.doctor_ops import failed_saves_finding

    db = tmp_path / "memory.db"
    db.write_bytes(b"not sqlite" * 100)
    status, detail, _fix = failed_saves_finding(db)
    assert status == "WARN" and "could not be read" in detail


def test_the_http_list_is_a_503_when_the_store_cannot_be_read(matrix, monkeypatch):
    app, client, who, _ = matrix
    import superlocalmemory.core.ops_remediation as remediation

    monkeypatch.setattr(remediation, "list_failed_operations",
                        lambda *a, **k: {"success": False, "unavailable": ["dead_letter"]})
    r = client.get("/operations/failed", headers=who["owner"])
    assert r.status_code == 503, r.text


# -- OPS-23 ------------------------------------------------------------------------


def test_retry_without_an_engine_keeps_the_failure_and_says_so(tmp_path):
    db = tmp_path / "memory.db"
    _seed_dead_letter(db, "default", "op-offline")
    result = resolve_operation(db, None, "op-offline", "retry")
    assert result["success"] is False
    assert result["code"] == "engine_unavailable" and result["retryable"] is True
    assert _dead_letter_rows(db, "op-offline") == 1


# -- OPS-22 ------------------------------------------------------------------------


@pytest.fixture
def ops(matrix):
    app, client, who, ids = matrix
    db_path = app.state.config.db_path
    _seed_dead_letter(db_path, "sales", "op-sales")
    _seed_dead_letter(db_path, "default", "op-default", error="default error")
    app.state.rbac.set_require_login(True)
    return app, client, who, db_path


def test_an_admin_of_default_lists_only_default(ops):
    app, client, who, _ = ops
    listed = client.get("/operations/failed", headers=who["admin_a"])
    assert listed.status_code == 200, listed.text
    assert {row["operation_id"] for row in listed.json()["dead_letter"]} == {"op-default"}
    assert "PRIVATE_SALES_ERROR" not in listed.text
    assert client.get("/operations/failed", params={"profile": "sales"},
                      headers=who["admin_a"]).status_code == 403


def test_an_admin_of_default_cannot_cancel_a_sales_failure(ops):
    app, client, who, db_path = ops
    r = client.post("/operations/op-sales/resolve", json={"action": "cancel"},
                    headers=who["admin_a"])
    assert r.status_code == 404, r.text
    assert _dead_letter_rows(db_path, "op-sales") == 1


def test_the_admin_of_sales_cancels_it_and_the_audit_names_who_and_where(ops):
    from superlocalmemory.compliance.audit import AuditChain
    from superlocalmemory.infra.data_root import state_path

    app, client, who, db_path = ops
    r = client.post("/operations/op-sales/resolve", json={"action": "cancel"},
                    headers={**who["admin_b"], "X-Actor-Id": "someone-else"})
    assert r.status_code == 200 and r.json()["success"] is True, r.text
    assert _dead_letter_rows(db_path, "op-sales") == 0
    assert _dead_letter_rows(db_path, "op-default") == 1
    entry = AuditChain(str(state_path("audit_chain.db"))).query(operation="ops_resolve:cancel")[0]
    assert entry["agent_id"].startswith("user:") and entry["agent_id"] != "user:someone-else"
    assert entry["profile_id"] == "sales"
    assert json.loads(entry["metadata"])["target_profiles"] == ["sales"]


def test_a_viewer_and_a_member_cannot_list(ops):
    app, client, who, _ = ops
    for name in ("viewer_a", "member_a"):
        assert client.get("/operations/failed", headers=who[name]).status_code == 403, name


def test_the_owner_sees_every_workspace_in_personal_mode(ops):
    app, client, who, _ = ops
    app.state.rbac.set_require_login(False)
    listed = client.get("/operations/failed", headers=who["owner"]).json()
    assert {row["operation_id"] for row in listed["dead_letter"]} == {"op-default", "op-sales"}
