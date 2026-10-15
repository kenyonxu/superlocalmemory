# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""OPS-08/17: an inventory entry is not proof that this callable is gated."""

# ruff: noqa: F811 (pytest fixtures are imported and redeclared as parameters)

from __future__ import annotations

import asyncio

import pytest

from superlocalmemory.core.admission import OperationKind, admits
from superlocalmemory.mcp.http_transport import SLMFastMCP
from tests.test_governance.test_role_surface_matrix_mcp import store  # noqa: F401


@pytest.mark.parametrize("flavor,first_is_admitted", [
    ("async", False), ("async", True), ("sync", False),
])
@pytest.mark.parametrize("registration", ["add_tool", "tool"])
def test_each_server_registration_gates_its_own_callable(
    store, flavor, first_is_admitted, registration,
):
    rbac, _ = store
    rbac.set_require_login(True)
    called = []

    if flavor == "async":
        async def registration_probe():
            called.append(True)
            return {"success": True, "private": "fixture-only"}
    else:
        def registration_probe():
            called.append(True)
            return {"success": True, "private": "fixture-only"}

    first = SLMFastMCP("first")
    first_fn = admits(OperationKind.RECALL)(registration_probe) \
        if first_is_admitted else registration_probe
    second = SLMFastMCP("second")
    if registration == "add_tool":
        first.add_tool(first_fn)
        second.add_tool(registration_probe)
    else:
        first.tool()(first_fn)
        second.tool()(registration_probe)

    for server in (first, second):
        result = asyncio.run(server.call_tool("registration_probe", {}))
        assert result.is_error is True
        assert result.structured_content["code"] == "NOT_AUTHORIZED"
    assert called == []
