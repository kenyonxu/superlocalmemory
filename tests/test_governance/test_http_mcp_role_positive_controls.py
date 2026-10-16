# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""The MCP login check preserves usable viewer, member, admin and owner access."""

# ruff: noqa: F811 (pytest fixtures are imported and redeclared as parameters)

from __future__ import annotations

import json

import pytest

from tests.test_governance.test_role_surface_matrix_mcp import (
    _call,
    http_client,  # noqa: F401
    store,  # noqa: F401
)


@pytest.mark.parametrize("company", [True, False], ids=["company", "personal"])
@pytest.mark.parametrize("role", ["anonymous", "owner", "viewer", "member", "admin"])
def test_http_mcp_read_and_write_have_the_expected_role_outcome(http_client, company, role):
    client, rbac, ids = http_client
    rbac.set_require_login(company)
    headers = {}
    if role == "owner":
        from superlocalmemory.core.security_primitives import ensure_install_token

        headers["X-Install-Token"] = ensure_install_token()
    elif role in {"viewer", "member", "admin"}:
        if role == "member":
            uid = rbac.create_user("member_a", "password-1234")["user_id"]
            rbac.set_membership("default", uid, "member")
        else:
            uid = ids[f"{role}_a"]
        headers["X-SLM-User-Session"] = rbac.create_session(uid)
    for tool, allowed in (
        ("wp9_probe_read", role in {"viewer", "member", "admin"} or not company),
        ("wp9_probe_write", role in {"member", "admin"} or
         (not company and role in {"anonymous", "owner"})),
    ):
        result = _call(client, tool, headers=headers)
        assert result["isError"] is (not allowed), (role, tool, result)
        if allowed:
            assert json.loads(result["content"][0]["text"])["success"] is True
        else:
            assert result["structuredContent"]["code"] == "NOT_AUTHORIZED"
