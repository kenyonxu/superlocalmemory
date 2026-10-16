# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""PR 248 review, finding 2: one person's switch must not move anyone else.

The active workspace was one daemon-wide value any reader could move: when
``dual`` entered ``sales``, ``viewer_a`` was refused its own workspace on the
next request. A signed-in person now has their own active workspace, bound
to their session; the daemon-wide value stays the machine owner's. The gates
and the handler read one value pinned per request, so a switch landing
between them is refused instead of serving a workspace nobody checked.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

from superlocalmemory.server.profile_runtime import get_profile_runtime
from tests.test_governance.test_role_surface_matrix_rest import _company, matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def _active(client, headers) -> str:
    return client.get("/api/profiles", headers=headers).json()["active_profile"]


def test_the_reviewers_reproduction_one_switch_does_not_lock_others_out(matrix):
    app, client, who, _ = matrix
    _company(app)
    assert client.get("/api/memories", headers=who["viewer_a"]).status_code == 200
    switched = client.post("/api/profiles/sales/switch", headers=who["dual"])
    assert switched.status_code == 200, switched.text[:200]
    after = client.get("/api/memories", headers=who["viewer_a"])
    assert after.status_code == 200, after.text[:200]
    assert _active(client, who["viewer_a"]) == "default"
    assert _active(client, who["dual"]) == "sales"
    assert get_profile_runtime(app.state).snapshot.profile_id == "default"


def test_each_person_starts_in_a_workspace_they_can_read(matrix):
    app, client, who, _ = matrix
    _company(app)
    assert _active(client, who["admin_b"]) == "sales"
    assert client.get("/api/memories", headers=who["admin_b"]).status_code == 200
    assert _active(client, who["viewer_a"]) == "default"


def test_the_owner_still_moves_the_daemon_in_personal_mode(matrix):
    app, client, who, _ = matrix
    r = client.post("/api/profiles/sales/switch", headers=who["owner"])
    assert r.status_code == 200, r.text[:200]
    assert get_profile_runtime(app.state).snapshot.profile_id == "sales"
    assert _active(client, who["owner"]) == "sales"


def test_a_switch_binds_only_the_session_that_made_it(matrix):
    app, client, who, ids = matrix
    _company(app)
    second = {**who["owner"], "X-SLM-User-Session": app.state.rbac.create_session(ids["dual"])}
    client.post("/api/profiles/sales/switch", headers=who["dual"])
    assert _active(client, who["dual"]) == "sales"
    assert _active(client, second) == "default"


def test_a_save_or_recall_naming_no_workspace_uses_the_callers(matrix):
    app, client, who, _ = matrix
    _company(app)
    client.post("/api/profiles/sales/switch", headers=who["dual"])
    served = client.get("/recall", params={"q": ""}, headers=who["dual"])
    assert served.status_code == 200, served.text[:200]
    assert served.json()["profile"] == "sales"
    own = client.get("/recall", params={"q": ""}, headers=who["viewer_a"])
    assert own.json()["profile"] == "default"


def test_losing_the_membership_falls_back_and_logout_forgets(matrix):
    app, client, who, ids = matrix
    _company(app)
    client.post("/api/profiles/sales/switch", headers=who["dual"])
    app.state.rbac.set_membership("sales", ids["admin_b"], "admin")  # keep an admin
    app.state.rbac.remove_membership("sales", ids["dual"])
    assert _active(client, who["dual"]) == "default"
    assert client.post("/api/rbac/logout", headers=who["dual"]).status_code == 200
    from superlocalmemory.server.caller_workspace import session_workspaces

    token = who["dual"]["X-SLM-User-Session"]
    assert session_workspaces(app.state).get(token) is None


def test_a_daemon_switch_never_changes_what_a_signed_in_request_acts_on(matrix):
    """The gate and the handler read one pinned value: the caller's own."""
    app, client, who, _ = matrix
    _company(app)
    assert client.get("/recall", params={"q": ""}, headers=who["viewer_a"]).json()["profile"] \
        == "default"
    from tests.test_server.test_rbac_company_mode_credential import _daemon_headers

    moved = client.post("/api/profiles/sales/switch", headers=_daemon_headers(app))
    assert moved.status_code == 200, moved.text[:200]
    assert get_profile_runtime(app.state).snapshot.profile_id == "sales"
    served = client.get("/recall", params={"q": ""}, headers=who["viewer_a"])
    assert served.status_code == 200, served.text[:200]
    assert served.json()["profile"] == "default"
    assert client.get("/api/memories", headers=who["viewer_a"]).status_code == 200
