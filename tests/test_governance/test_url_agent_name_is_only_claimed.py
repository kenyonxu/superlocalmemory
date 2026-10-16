# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""PR 248 review, finding 3: a name in the MCP URL is a claim, not a connection.

Over HTTP MCP the "connection" name was the path segment of the URL the caller
posted to (``/mcp/claude-code/``). No credential binds it, yet it reached the
daemon in ``X-SLM-Connection-Agent`` next to the capability the daemon's own
MCP process holds, and the save was stored as ``agent_id_source=connection``,
which the dashboard shows as vouched. Now only a request that presents the
daemon capability vouches for its URL name; any other URL name is recorded as
claimed. The stdio server's configured ``SLM_AGENT_ID`` is unchanged.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_governance.test_role_surface_matrix_mcp import store  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import LOOPBACK

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
     "MCP-Protocol-Version": "2025-06-18"}


class _Descriptor:
    capability = "cap-" + "x" * 40
    instance_id = "instance-1"
    capability_fingerprint = "fp"


def _app():
    from superlocalmemory.core.agent_attribution import connection_agent, forwarded_headers
    from superlocalmemory.core.caller_session import CallerSessionASGI
    from superlocalmemory.mcp.agent_context import AgentIDExtractorASGI, get_current_agent_id
    from superlocalmemory.mcp.http_transport import SLMFastMCP
    from superlocalmemory.server.remote_tool_policy import RemoteToolScopeASGI

    probe = SLMFastMCP("probe")

    @probe.tool()
    async def who() -> dict:
        return {"success": True, "connection": connection_agent(),
                "claimed": get_current_agent_id(),
                "forwarded": forwarded_headers("daemon-capability")}

    mcp_app = probe.streamable_http_app(streamable_http_path="/", stateless_http=True,
                                        json_response=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.daemon_descriptor = _Descriptor()
    app.mount("/mcp", RemoteToolScopeASGI(CallerSessionASGI(AgentIDExtractorASGI(mcp_app))))
    return app


def _who(client, path, headers=None) -> dict:
    r = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "who", "arguments": {}}},
                    headers={**H, **(headers or {})})
    assert r.status_code == 200, r.text[:300]
    return json.loads(r.json()["result"]["content"][0]["text"])


def test_the_reviewers_reproduction_a_url_name_without_a_credential(store):
    rbac, _ = store
    rbac.set_require_login(False)
    with TestClient(_app(), base_url="http://127.0.0.1:8765", client=LOOPBACK) as c:
        seen = _who(c, "/mcp/claude-code/")
    assert seen["connection"] == ""          # not vouched
    assert seen["claimed"] == "claude-code"  # still recorded, as a claim
    assert seen["forwarded"] == {}           # never sent as the connection


def test_a_url_name_with_the_daemon_capability_is_the_connection(store):
    rbac, _ = store
    rbac.set_require_login(False)
    cap = {"X-SLM-Daemon-Capability": _Descriptor.capability,
           "X-SLM-Target-Instance": _Descriptor.instance_id}
    with TestClient(_app(), base_url="http://127.0.0.1:8765", client=LOOPBACK) as c:
        seen = _who(c, "/mcp/claude-code/", cap)
        wrong = _who(c, "/mcp/claude-code/", {**cap, "X-SLM-Daemon-Capability": "guess"})
    assert seen["connection"] == "claude-code"
    assert seen["forwarded"] == {"X-SLM-Connection-Agent": "claude-code"}
    assert wrong["connection"] == ""


def test_the_stdio_configured_name_is_still_the_connection(monkeypatch):
    from superlocalmemory.core.agent_attribution import connection_agent

    monkeypatch.setenv("SLM_AGENT_ID", "codex")
    assert connection_agent() == "codex"


@pytest.mark.parametrize("vouched", [False, True], ids=["url-only", "capability"])
def test_the_saved_record_says_claimed_for_a_url_name(engine_with_mock_deps, vouched):
    """End to end: the URL name, the headers the MCP process sends, the saved row."""
    from superlocalmemory.core.agent_attribution import forwarded_headers
    from superlocalmemory.mcp import agent_context
    from tests.test_server.test_canonical_remember_route import _client

    token = agent_context._current_agent_id.set("claude-code")
    vouch = agent_context._agent_id_vouched.set(vouched)
    try:
        sent = forwarded_headers("daemon-capability")
    finally:
        agent_context._agent_id_vouched.reset(vouch)
        agent_context._current_agent_id.reset(token)
    engine = engine_with_mock_deps
    with _client(engine) as client:  # the daemon's own MCP process: capability held
        r = client.post("/remember?wait=true", headers=sent,
                        json={"content": "Atlas ships Friday.", "idempotency_key": "url-claim",
                              "metadata": {"agent_id": "claude-code"}})
        assert r.status_code == 200, r.text[:300]
    op = dict(engine._db.execute("SELECT * FROM ingestion_operations")[0])
    meta = json.loads(op["raw_metadata_json"])
    assert meta["agent_id"] == "claude-code"
    assert meta["agent_id_source"] == ("connection" if vouched else "claimed")
