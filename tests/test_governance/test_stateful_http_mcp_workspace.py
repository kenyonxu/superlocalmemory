"""Stateful transport restores each request's user and workspace context."""

# ruff: noqa: F811
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_governance.test_role_surface_matrix_mcp import store  # noqa: F401


def test_stateful_request_context_and_switch(store):
    from superlocalmemory.core.admission import admits
    from superlocalmemory.core.caller_session import CallerSessionASGI
    from superlocalmemory.core.operation_request import OperationKind
    from superlocalmemory.mcp.http_transport import SLMFastMCP
    from superlocalmemory.server.caller_workspace import (
        CallerWorkspaceMiddleware,
        switch_http_workspace,
    )
    from superlocalmemory.server.profile_runtime import current_request_profile

    rbac, ids = store
    rbac.set_require_login(True)
    rbac.set_membership("sales", ids["admin_a"], "viewer")
    probe = SLMFastMCP("N1 stateful review")

    @probe.tool()
    @admits(OperationKind.RECALL)
    async def switch_profile(profile_id: str):
        return {"success": switch_http_workspace(profile_id)}

    @probe.tool()
    async def read_active() -> dict:
        return {"profile_id": current_request_profile()}

    @probe.resource("slm://test")
    async def read_resource():
        return current_request_profile()

    mcp_app = probe.streamable_http_app(
        streamable_http_path="/", stateless_http=False, json_response=True
    )

    @asynccontextmanager
    async def lifespan(_):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.engine = SimpleNamespace(profile_id="default")
    app.state.rbac = rbac
    app.add_middleware(CallerWorkspaceMiddleware, app_state=app.state)
    app.mount("/mcp", CallerSessionASGI(mcp_app))
    base_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    tokens = {name: rbac.create_session(ids[name]) for name in ("admin_a", "viewer_a")}
    transport_ids = {}
    with TestClient(app, base_url="http://127.0.0.1:9911") as client:

        def rpc(user, method, params, notification=False):
            headers = {**base_headers, "X-SLM-User-Session": tokens[user]}
            if user in transport_ids:
                headers["Mcp-Session-Id"] = transport_ids[user]
            body = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notification:
                body["id"] = 1
            response = client.post("/mcp/", headers=headers, json=body)
            if method == "initialize":
                transport_ids[user] = response.headers["mcp-session-id"]
            assert response.status_code in (200, 202), response.text
            return None if notification else response.json()

        for user in tokens:
            rpc(
                user,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": user, "version": "1"},
                },
            )
            rpc(user, "notifications/initialized", {}, notification=True)
        switched = rpc(
            "admin_a",
            "tools/call",
            {"name": "switch_profile", "arguments": {"profile_id": "sales"}},
        )
        assert switched["result"]["isError"] is False, switched
        for user, expected in (("admin_a", "sales"), ("viewer_a", "default")):
            result = rpc(user, "tools/call", {"name": "read_active", "arguments": {}})
            assert json.loads(result["result"]["content"][0]["text"])["profile_id"] == expected, (
                result
            )
            result = rpc(user, "resources/read", {"uri": "slm://test"})
            assert result["result"]["contents"][0]["text"] == expected, result
        transport_ids["viewer_a"] = transport_ids["admin_a"]
        result = rpc("viewer_a", "tools/call", {"name": "read_active", "arguments": {}})
        assert json.loads(result["result"]["content"][0]["text"])["profile_id"] == "default", result
