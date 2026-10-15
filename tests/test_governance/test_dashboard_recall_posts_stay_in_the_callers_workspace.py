# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Recall Lab, Memories search and memory chat answer from the caller's workspace.

These three are POST routes, so the GET-only RBAC middleware does not gate them.
Each checked READ on the caller's workspace and then asked the engine without
naming one, so the engine searched its own active workspace: a person signed in
to ``sales`` read the owner's ``default`` memories. Found by signing in as two
users on a test install (13 Oct 2026).
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import pytest

from tests.test_governance.test_role_surface_matrix_rest import _company, matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401

DEFAULT_ONLY = "Kestrel launch date is Friday in the default workspace."
SALES_ONLY = "Acme invoices are approved by Priya in the sales workspace."
QUESTION = "Kestrel launch Acme invoices approved"

ROUTES = [
    ("trace", "/api/v3/recall/trace", {"query": QUESTION, "limit": 10}),
    ("search", "/api/search", {"query": QUESTION, "limit": 10}),
    ("chat", "/api/v3/chat/stream", {"query": QUESTION, "limit": 10}),
]


@pytest.fixture
def two_workspaces(matrix):
    app, client, who, ids = matrix
    engine = app.state.engine
    engine.store(DEFAULT_ONLY, profile_id="default")
    engine.store(SALES_ONLY, profile_id="sales")
    _company(app)
    return app, client, who


@pytest.mark.parametrize("name,path,body", ROUTES, ids=[r[0] for r in ROUTES])
def test_a_sales_admin_never_reads_the_default_workspace(two_workspaces, name, path, body):
    _, client, who = two_workspaces
    res = client.post(path, json=body, headers=who["admin_b"])
    assert res.status_code == 200, res.text[:300]
    assert "Friday in the default workspace" not in res.text, f"{name} leaked the default workspace"


@pytest.mark.parametrize("name,path,body", ROUTES, ids=[r[0] for r in ROUTES])
def test_a_default_viewer_never_reads_the_sales_workspace(two_workspaces, name, path, body):
    _, client, who = two_workspaces
    res = client.post(path, json=body, headers=who["viewer_a"])
    assert res.status_code == 200, res.text[:300]
    assert "approved by Priya" not in res.text, f"{name} leaked the sales workspace"


def test_each_person_finds_their_own_workspace_in_the_recall_lab(two_workspaces):
    _, client, who = two_workspaces
    sales = client.post("/api/v3/recall/trace", json={"query": "who approves Acme invoices",
                                                      "limit": 5}, headers=who["admin_b"])
    assert "approved by Priya" in sales.text
    default = client.post("/api/v3/recall/trace", json={"query": "Kestrel launch date",
                                                        "limit": 5}, headers=who["viewer_a"])
    assert "Friday in the default workspace" in default.text


def test_signed_out_callers_are_refused(two_workspaces):
    _, client, who = two_workspaces
    for _, path, body in ROUTES:
        assert client.post(path, json=body, headers=who["anonymous"]).status_code in (401, 403)


def test_one_memory_by_id_opens_only_inside_the_callers_workspace(two_workspaces):
    """``GET /api/memories?fact_id=`` (a source opened from the dashboard)."""
    app, client, who = two_workspaces
    rows = app.state.engine._db.execute(
        "SELECT fact_id, profile_id FROM atomic_facts WHERE content LIKE ?", ("%Acme invoices%",))
    sales_fact = dict(rows[0])["fact_id"]
    own = client.get("/api/memories", params={"fact_id": sales_fact, "limit": 1},
                     headers=who["admin_b"])
    assert own.status_code == 200 and [m["id"] for m in own.json()["memories"]] == [sales_fact]
    other = client.get("/api/memories", params={"fact_id": sales_fact, "limit": 1},
                       headers=who["viewer_a"])
    assert other.status_code == 200 and other.json()["memories"] == []
    bad = client.get("/api/memories", params={"fact_id": "x' OR 1=1 --"}, headers=who["admin_b"])
    assert bad.status_code == 422
