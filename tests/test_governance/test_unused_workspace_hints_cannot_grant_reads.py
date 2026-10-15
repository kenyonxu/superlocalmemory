# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""OPS-25: a permitted decoy query cannot authorize a handler's active workspace."""

# ruff: noqa: F811 (pytest fixtures imported and redeclared as parameters)

import pytest

from superlocalmemory.storage.models import AtomicFact, MemoryRecord
from tests.test_governance.test_role_surface_matrix_rest import matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.mark.parametrize("company", [True, False], ids=["company", "personal"])
@pytest.mark.parametrize("key", ["profile", "profile_id"])
@pytest.mark.parametrize("path", [
    "/api/memories", "/api/agents/memory-activity",
    "/api/compliance/status", "/api/optimize/config",
])
def test_an_ignored_query_hint_cannot_replace_the_workspace_actually_read(
    matrix, company, key, path, monkeypatch,
):
    app, client, who, _ = matrix
    app.state.rbac.set_require_login(company)
    db = app.state.engine._db
    memory_id = db.store_memory(MemoryRecord(
        profile_id="default", content="DEFAULT_PRIVATE_SENTINEL",
    ))
    db.store_fact(AtomicFact(
        fact_id="private-default", profile_id="default", memory_id=memory_id,
        content="DEFAULT_PRIVATE_SENTINEL",
    ))
    # admin_b's own active workspace is ``sales`` (bound to the session, PR 248
    # finding 2), so a hint naming sales is honest here; the sentinel in
    # ``default`` must never be served. Pin the caller to the owner's
    # workspace to keep the decoy shape: hint = sales, read = default.
    from superlocalmemory.server import caller_workspace

    monkeypatch.setattr(caller_workspace, "resolve_caller_workspace",
                        lambda scope, state: caller_workspace.CallerWorkspace("default", False))
    response = client.get(path, params={key: "sales"}, headers=who["admin_b"])
    assert response.status_code == 403, (path, key, response.status_code, response.text)
    assert "DEFAULT_PRIVATE_SENTINEL" not in response.text


@pytest.mark.parametrize("path,key", [
    ("/api/v3/timeline", "profile"),
    ("/api/v3/timeline/", "profile"), ("/api/tiers/stats", "profile_id"),
])
def test_a_consumed_selector_still_allows_a_sales_only_account(matrix, path, key):
    app, client, who, _ = matrix
    app.state.rbac.set_require_login(True)
    response = client.get(path, params={key: "sales"}, headers=who["admin_b"])
    assert response.status_code == 200, response.text
    assert app.state.engine.profile_id == "default"  # a read never switches the active workspace
