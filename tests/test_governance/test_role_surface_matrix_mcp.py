# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Role x surface matrix, MCP over HTTP and stdio (4.1.25 WP-9).

* OPS-16: a session REST accepts must be the same identity over HTTP MCP.
* OPS-08 / OPS-17: every MCP tool and resource asks who is calling; only the
  listed public tools answer an anonymous caller where a login is required,
  and a ``profile_id`` naming a workspace the caller cannot read is refused.
* UX-13: a refused (or failed) tool call is an MCP tool error, ``isError``.

The full inventory runs in a child process with every tool registered
(``SLM_MCP_ALL_TOOLS=1``), because which tools the shared server registers is
decided once, at import, by the environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from superlocalmemory.mcp.tool_gate import PUBLIC_MCP_TOOLS


def _rbac_store(root):
    """memory.db with the schema and role tables in the data root."""
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.migrations import M024_rbac_users_roles as m024

    root.mkdir(parents=True, exist_ok=True)
    db = root / "memory.db"
    conn = sqlite3.connect(db)
    schema.create_all_tables(conn)
    m024.apply(conn)
    conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('default','default')")
    conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('sales','sales')")
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def store(monkeypatch):
    from superlocalmemory.access.rbac import RbacEngine
    from superlocalmemory.infra.data_root import canonical_data_root

    monkeypatch.delenv("SLM_USER_SESSION", raising=False)
    db = _rbac_store(canonical_data_root())
    rbac = RbacEngine(str(db))
    ids = {}
    for name, profile, role in (("viewer_a", "default", "viewer"), ("admin_a", "default", "admin"),
                                ("admin_b", "sales", "admin")):
        ids[name] = rbac.create_user(name, "password-1234")["user_id"]
        rbac.set_membership(profile, ids[name], role)
    return rbac, ids


# -- the whole inventory, in a child process -------------------------------------------

_WALK = r"""
import asyncio, json, os, sys
from superlocalmemory.access.rbac import RbacEngine
from superlocalmemory.infra.data_root import canonical_data_root
from superlocalmemory.mcp.server import server
from superlocalmemory.core.caller_session import bind_session
from superlocalmemory.core.admission import _GATED_MCP_TOOLS

rbac = RbacEngine(str(canonical_data_root() / "memory.db"))
rbac.set_require_login(True)
tokens = {"anonymous": "", "viewer_a": sys.argv[1]}
DUMMY = {"string": "x", "integer": 1, "number": 1.0, "boolean": False, "array": [], "object": {}}

def args_for(tool, foreign):
    props = tool.parameters.get("properties", {})
    out = {}
    for name in tool.parameters.get("required", []):
        kind = props.get(name, {}).get("type", "string")
        out[name] = DUMMY.get(kind if isinstance(kind, str) else "string", "x")
    if foreign and "profile_id" in props:
        out["profile_id"] = "sales"
    return out

async def walk():
    tools = server._tool_manager._tools
    report = {"tools": sorted(tools),
              "ungated": sorted(n for n in tools if n not in _GATED_MCP_TOOLS),
              "answered": {}, "not_error": {}}
    for who, token in tokens.items():
        bind_session(token)
        for name, tool in sorted(tools.items()):
            if who == "viewer_a" and "profile_id" not in tool.parameters.get("properties", {}):
                continue
            result = await server.call_tool(name, args_for(tool, foreign=(who == "viewer_a")))
            body = result.structured_content or {}
            if body.get("code") != "NOT_AUTHORIZED":
                report["answered"].setdefault(who, []).append(name)
            elif not result.is_error:
                report["not_error"].setdefault(who, []).append(name)
    bind_session("")
    resources = {}
    for uri in ("slm://recent", "slm://stats", "slm://context"):
        try:
            await server.read_resource(uri)
            resources[uri] = "answered"
        except Exception as exc:
            data = getattr(getattr(exc, "error", None), "data", None) or {}
            resources[uri] = "refused" if data.get("code") == "NOT_AUTHORIZED" else repr(exc)
    report["resources"] = resources
    print("REPORT" + json.dumps(report))

asyncio.run(walk())
"""


@pytest.fixture
def walk_report(store):
    rbac, ids = store
    env = {**os.environ, "SLM_MCP_ALL_TOOLS": "1", "SLM_DAEMON_PORT": "1"}
    env.pop("SLM_USER_SESSION", None)
    done = subprocess.run(
        [sys.executable, "-c", _WALK, rbac.create_session(ids["viewer_a"])],
        env=env, capture_output=True, text=True, timeout=600,
    )
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("REPORT")), None)
    assert line, {"returncode": done.returncode, "stderr": done.stderr[-3000:]}
    return json.loads(line[len("REPORT"):])


def test_every_tool_is_gated_or_listed_public_with_a_reason(walk_report):
    assert len(walk_report["tools"]) > 100, "the walk did not see the whole inventory"
    assert walk_report["ungated"] == sorted(PUBLIC_MCP_TOOLS)
    assert all(reason.strip() for reason in PUBLIC_MCP_TOOLS.values())


def test_an_anonymous_caller_gets_only_the_public_tools(walk_report):
    """OPS-08 / OPS-17: slm_cache_get, get_assertions, get_status, mesh_peers ..."""
    assert sorted(walk_report["answered"].get("anonymous", [])) == sorted(PUBLIC_MCP_TOOLS)


def test_a_viewer_naming_another_workspace_is_refused_by_every_tool(walk_report):
    assert walk_report["answered"].get("viewer_a", []) == []


def test_every_refusal_is_an_mcp_tool_error(walk_report):
    """UX-13: refusals reach the client with isError, not as a successful call."""
    assert walk_report["not_error"] == {}


def test_resources_need_a_session_where_login_is_required(walk_report):
    assert set(walk_report["resources"].values()) == {"refused"}, walk_report["resources"]


# -- HTTP MCP: the session the request carries is the caller --------------------------


def _probe_app():
    """The production MCP mount (remote scope, session binder, agent router)
    over a server with one RECALL-gated probe and one failing tool."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from superlocalmemory.core.admission import admits
    from superlocalmemory.core.caller_session import CallerSessionASGI
    from superlocalmemory.core.operation_request import OperationKind
    from superlocalmemory.mcp.agent_context import AgentIDExtractorASGI
    from superlocalmemory.mcp.http_transport import SLMFastMCP
    from superlocalmemory.server.remote_tool_policy import RemoteToolScopeASGI

    probe = SLMFastMCP("probe")

    @probe.tool()
    @admits(OperationKind.RECALL)
    async def wp9_probe_read(profile_id: str = "") -> dict:
        return {"success": True, "read": profile_id or "active"}

    @probe.tool()
    @admits(OperationKind.REMEMBER)
    async def wp9_probe_write(content: str = "x") -> dict:
        return {"success": True}

    @probe.tool()
    async def wp9_probe_bad_kind(kind: str = "banana") -> dict:
        return {"success": False, "code": "INVALID_KIND", "retryable": False, "error": kind}

    mcp_app = probe.streamable_http_app(streamable_http_path="/", stateless_http=True,
                                        json_response=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", RemoteToolScopeASGI(CallerSessionASGI(AgentIDExtractorASGI(mcp_app))))
    return app


def _call(client, tool, arguments=None, headers=None, cookies=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}}}
    if cookies:
        client.cookies.update(cookies)
    r = client.post("/mcp/", json=body, headers={
        "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18", **(headers or {})})
    client.cookies.clear()
    assert r.status_code == 200, r.text[:300]
    return r.json()["result"]


@pytest.fixture
def http_client(store):
    from fastapi.testclient import TestClient

    from tests.test_server.test_rbac_company_mode_credential import LOOPBACK

    rbac, ids = store
    rbac.set_require_login(True)
    with TestClient(_probe_app(), base_url="http://127.0.0.1:8765", client=LOOPBACK) as client:
        yield client, rbac, ids


def _refused(result) -> bool:
    return result.get("isError") is True and \
        result.get("structuredContent", {}).get("code") == "NOT_AUTHORIZED"


def test_a_valid_session_header_is_the_caller_over_http_mcp(http_client):
    """OPS-16: the audit's reproduction refused this as authentication_required."""
    client, rbac, ids = http_client
    session = {"X-SLM-User-Session": rbac.create_session(ids["viewer_a"])}
    ok = _call(client, "wp9_probe_read", headers=session)
    assert ok.get("isError") is False, ok
    assert json.loads(ok["content"][0]["text"])["read"] == "active"


def test_the_dashboard_cookie_is_the_caller_too(http_client):
    client, rbac, ids = http_client
    cookie = {"slm_session": rbac.create_session(ids["viewer_a"])}
    ok = _call(client, "wp9_probe_read", cookies=cookie)
    assert ok.get("isError") is False, ok


def test_the_callers_role_still_applies_over_http_mcp(http_client):
    client, rbac, ids = http_client
    viewer = {"X-SLM-User-Session": rbac.create_session(ids["viewer_a"])}
    assert _refused(_call(client, "wp9_probe_write", headers=viewer))
    assert _refused(_call(client, "wp9_probe_read", {"profile_id": "sales"}, headers=viewer))
    admin_b = {"X-SLM-User-Session": rbac.create_session(ids["admin_b"])}
    own = _call(client, "wp9_probe_read", {"profile_id": "sales"}, headers=admin_b)
    assert own["isError"] is False
    assert _refused(_call(client, "wp9_probe_read", headers=admin_b))  # not a member of default


def test_no_session_or_an_ended_one_is_refused_with_is_error(http_client):
    client, rbac, ids = http_client
    assert _refused(_call(client, "wp9_probe_read"))
    token = rbac.create_session(ids["viewer_a"])
    rbac.revoke_session(token)
    assert _refused(_call(client, "wp9_probe_read", headers={"X-SLM-User-Session": token}))


def test_the_daemons_own_environment_is_never_the_caller(http_client, monkeypatch):
    """An HTTP request with no session must not inherit SLM_USER_SESSION from
    the daemon's environment."""
    client, rbac, ids = http_client
    monkeypatch.setenv("SLM_USER_SESSION", rbac.create_session(ids["admin_a"]))
    assert _refused(_call(client, "wp9_probe_read"))


def test_a_failed_tool_call_is_an_mcp_tool_error(http_client):
    """UX-13: recall(kind="banana") answered success:false with isError:false."""
    client, rbac, ids = http_client
    rbac.set_require_login(False)
    result = _call(client, "wp9_probe_bad_kind")
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "INVALID_KIND"
    assert json.loads(result["content"][0]["text"])["retryable"] is False


# -- stdio MCP: the process's own SLM_USER_SESSION -------------------------------------


def test_stdio_uses_the_process_session(store, monkeypatch):
    from superlocalmemory.core.admission import admits
    from superlocalmemory.core.operation_request import OperationKind

    rbac, ids = store
    rbac.set_require_login(True)

    @admits(OperationKind.RECALL)
    async def wp9_stdio_probe(profile_id: str = "") -> dict:
        return {"success": True}

    assert asyncio.run(wp9_stdio_probe())["code"] == "NOT_AUTHORIZED"
    monkeypatch.setenv("SLM_USER_SESSION", rbac.create_session(ids["viewer_a"]))
    assert asyncio.run(wp9_stdio_probe())["success"] is True
    assert asyncio.run(wp9_stdio_probe(profile_id="sales"))["code"] == "NOT_AUTHORIZED"


def test_cached_originals_belong_to_the_signed_in_user(store, monkeypatch):
    """OPS-08: the cache tenant is bound to the person, not only to the agent
    segment a caller chooses."""
    from superlocalmemory.core.caller_session import bind_session, unbind_session
    from superlocalmemory.mcp.tools_optimize import _tenant

    rbac, ids = store
    assert _tenant() == "mcp_client"  # personal mode, no session: unchanged address
    rbac.set_require_login(True)
    tenants = []
    for name in ("viewer_a", "admin_a"):
        token = bind_session(rbac.create_session(ids[name]))
        try:
            tenants.append(_tenant())
        finally:
            unbind_session(token)
    assert tenants[0] != tenants[1]
    assert all(t.startswith("user:") for t in tenants)
