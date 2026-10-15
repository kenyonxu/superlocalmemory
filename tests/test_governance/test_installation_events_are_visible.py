# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""PR 248 review, finding 5: installation-wide access events reach the audit pane.

The login policy, account changes, sign-in and sign-out are written with no
workspace, and the pane showed only the active workspace's events, so the
evidence of finding 1's escalation sat where no administrator looked. They
are now recorded as installation-scope events and shown to whoever holds
authority over the whole installation (the owner, or an administrator of
every workspace), and to nobody else.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import json

import pytest

from tests.test_governance.test_role_surface_matrix_rest import (  # noqa: F401
    _company,
    _global_admin,
    matrix,
)
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.fixture
def audited(matrix, monkeypatch):
    """The audit route and the account routes write and read one chain."""
    from pathlib import Path

    from superlocalmemory.server.routes import compliance

    app, client, who, ids = matrix
    chain = Path(app.state.rbac._db_path).parent / "audit_chain.db"
    monkeypatch.setattr(compliance, "AUDIT_DB", chain)
    _global_admin(app, who, ids)
    return app, client, who, ids


def _events(client, headers) -> list[dict]:
    body = client.get("/api/compliance/audit", params={"limit": 500}, headers=headers).json()
    assert body.get("available") is True, body
    return body["events"]


def _policy_events(events) -> list[dict]:
    return [e for e in events if e["operation"] == "access.policy_change"]


def test_a_policy_change_is_recorded_as_installation_scope(audited):
    app, client, who, _ = audited
    r = client.post("/api/rbac/policy", json={"require_login": True}, headers=who["root_admin"])
    assert r.status_code == 200, r.text[:200]
    events = _policy_events(_events(client, who["root_admin"]))
    assert events, "the installation admin cannot see the policy change"
    for event in events:
        assert event["profile_id"] == ""
        assert json.loads(event["metadata"])["scope"] == "installation"
        assert "installation" in event["target"]


def test_the_owner_sees_installation_events_in_personal_mode(audited):
    app, client, who, _ = audited
    client.post("/api/rbac/policy", json={"require_login": False}, headers=who["owner"])
    assert _policy_events(_events(client, who["owner"]))


def test_a_workspace_admin_does_not_see_installation_events(audited):
    app, client, who, _ = audited
    client.post("/api/rbac/policy", json={"require_login": True}, headers=who["root_admin"])
    seen = _events(client, who["admin_a"])
    assert not _policy_events(seen)
    assert all(e["profile_id"] == "default" for e in seen)
