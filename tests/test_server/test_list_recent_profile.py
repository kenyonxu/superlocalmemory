# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Per-request profile on GET /list + result completeness (spec section 5).

Extends the section-3 routing contract (POST /remember, GET /recall) to the
list-recent read: a non-empty ``profile_id`` is pure routing — the request is
served against THAT profile without moving the ProfileRuntime active pointer
or its generation; an unknown profile is rejected 404 + ``unknown_profile``
and never implicitly created; an empty/absent ``profile_id`` keeps the legacy
active-profile behaviour byte-compatible. Results are complete: content is no
longer truncated at 100 chars and ``importance`` rides along with the
pre-existing fact_id / fact_type / created_at fields. An empty but real
namespace is a plain success with zero results, never an abstain.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from superlocalmemory.server.unified_daemon import create_app
from superlocalmemory.storage.migrations import (
    M018_ingestion_operations,
    M032_write_coordinator_admission,
    M033_projection_transactions,
    M034_obligation_integrity,
    M042_correction_case_ledger,
)

# Well past the pre-fix 100-char truncation boundary, so a truncated response
# cannot accidentally equal it.
LONG_CONTENT = (
    "Doris owns the release branch schedule and records every platform "
    "freeze window, including the northern region rollout calendar and the "
    "quarterly audit buffers for the on-call rotation."
)

A_ONLY_CONTENT = (
    "Zebra coordinates the zonal inventory audit and keeps the northern "
    "warehouse ledger for profile a alone."
)


@contextmanager
def _daemon(engine, profiles=("a", "b", "c")):
    """TestClient daemon with pre-created profiles; "c" is left empty.

    Mirrors ``test_per_request_profile._daemon``: the daemon-owned canonical
    writer is injected because TestClient does not enter lifespan.
    """
    from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime

    with engine._db.raw_connection() as conn:
        M018_ingestion_operations.apply(conn)
        M032_write_coordinator_admission.apply(conn)
        M033_projection_transactions.apply(conn)
        M034_obligation_integrity.apply(conn)
        M042_correction_case_ledger.apply(conn)
        for profile_id in profiles:
            conn.execute(
                "INSERT OR IGNORE INTO profiles (profile_id, name) "
                "VALUES (?, ?)",
                (profile_id, f"Profile {profile_id}"),
            )
        conn.commit()
    app = create_app()
    app.state.engine = engine
    runtime = CanonicalRememberRuntime.for_engine(engine)
    runtime.start()
    app.state.canonical_remember_runtime = runtime
    client = TestClient(app)
    client.headers["X-SLM-Daemon-Capability"] = (
        app.state.daemon_descriptor.capability
    )
    client.headers["X-SLM-Target-Instance"] = (
        app.state.daemon_descriptor.instance_id
    )
    try:
        yield client, app
    finally:
        runtime.stop()


@pytest.fixture
def daemon(engine_with_mock_deps):
    with _daemon(engine_with_mock_deps) as pair:
        yield pair


def _remember(
    client, content: str, profile_id: str = "", key: str = "",
    session_id: str = "",
) -> None:
    payload: dict = {"content": content}
    if profile_id:
        payload["profile_id"] = profile_id
    if key:
        payload["idempotency_key"] = key
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/remember", json=payload)
    assert response.status_code == 200, response.text


class TestListRecentRouting:
    def test_routes_to_explicit_profile(self, daemon) -> None:
        client, _ = daemon
        _remember(client, LONG_CONTENT, profile_id="b", key="list-route-b-1")

        response = client.get("/list", params={"profile_id": "b", "limit": 1})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["profile"] == "b"
        assert body["count"] == len(body["results"]) >= 1
        item = body["results"][0]
        assert item["content"] == LONG_CONTENT  # not truncated at 100 chars
        assert item["fact_id"]
        assert item["fact_type"]
        assert item["created_at"]
        assert "importance" in item

    def test_isolation(self, daemon) -> None:
        client, _ = daemon
        _remember(
            client, "doris only: " + LONG_CONTENT,
            profile_id="b", key="list-iso-b-1",
        )
        _remember(client, A_ONLY_CONTENT, profile_id="a", key="list-iso-a-1")

        response = client.get("/list", params={"profile_id": "a"})

        assert response.status_code == 200, response.text
        contents = [item["content"] for item in response.json()["results"]]
        assert A_ONLY_CONTENT in contents, (
            "the routed list must actually read profile a"
        )
        assert all("doris only" not in content for content in contents)

    def test_pointer_untouched(self, daemon) -> None:
        client, _ = daemon
        before = client.get("/status").json()

        routed = client.get("/list", params={"profile_id": "b"})

        after = client.get("/status").json()
        # The routed call must have actually routed, or the pointer
        # comparison below would pass vacuously.
        assert routed.status_code == 200, routed.text
        assert routed.json()["profile"] == "b"
        assert after["profile"] == before["profile"]
        assert after["profile_generation"] == before["profile_generation"]

    def test_unknown_profile_404(self, daemon) -> None:
        client, app = daemon
        engine = app.state.engine
        rows_before = int(dict(
            engine._db.execute("SELECT COUNT(*) AS c FROM profiles")[0]
        )["c"])

        response = client.get("/list", params={"profile_id": "ghost"})

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_profile"
        assert body["error"]["profile_id"] == "ghost"
        # No implicit creation: the profiles table is untouched.
        rows_after = int(dict(
            engine._db.execute("SELECT COUNT(*) AS c FROM profiles")[0]
        )["c"])
        assert rows_after == rows_before

    def test_empty_profile_legacy(self, daemon) -> None:
        client, _ = daemon
        _remember(client, LONG_CONTENT, key="list-legacy-1")
        active = client.get("/status").json()["profile"]

        response = client.get("/list", params={"limit": 1})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        # Empty profile_id lands on the active profile, as before.
        assert body["profile"] == active
        assert any(item["content"] == LONG_CONTENT for item in body["results"])

    def test_items_carry_session_id(self, daemon) -> None:
        # Spec section 3: session_id is a pre-existing item field the daemon
        # path must preserve, same as the offline engine path.
        client, _ = daemon
        _remember(
            client, LONG_CONTENT, profile_id="b",
            key="list-session-b-1", session_id="sess-list-1",
        )

        response = client.get("/list", params={"profile_id": "b", "limit": 1})

        assert response.status_code == 200, response.text
        item = response.json()["results"][0]
        assert item["session_id"] == "sess-list-1"

    def test_empty_namespace_success(self, daemon) -> None:
        client, _ = daemon

        response = client.get("/list", params={"profile_id": "c"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["results"] == []
        assert body["count"] == 0
        assert body["profile"] == "c"
        assert "abstain" not in body


# ---------------------------------------------------------------------------
# MCP tool surface: optional profile_id + result completeness
# ---------------------------------------------------------------------------


class _McpServerHarness:
    """Real-MCPServer harness for the core tool surface.

    Schema assertions read the live FastMCP-generated ``inputSchema`` (the
    upstream convention: the schema is derived from the registered
    signature); calls dispatch to the registered, admission-decorated
    function exactly as a connected MCP client would invoke it.
    """

    def __init__(self, engine) -> None:
        from mcp.server.mcpserver import MCPServer

        from superlocalmemory.mcp.tools_core import register_core_tools

        self._server = MCPServer("test-list-recent")
        register_core_tools(self._server, lambda: engine)
        self._tools = {
            tool.name: tool
            for tool in self._server._tool_manager.list_tools()
        }

    def call_tool(self, name: str, args: dict) -> dict:
        return asyncio.run(self._tools[name].fn(**args))

    def get_tool_schema(self, name: str) -> dict:
        return {"inputSchema": self._tools[name].parameters}


@pytest.fixture
def mcp_server(engine_with_mock_deps, monkeypatch):
    """Core tools registered against the mock-deps engine, daemon offline.

    The daemon probe is pinned OFF by default so these tests can never
    reach a resident daemon; daemon-path tests install their own double.
    """
    monkeypatch.setattr(
        "superlocalmemory.cli.daemon.is_daemon_running", lambda: False,
    )
    return _McpServerHarness(engine_with_mock_deps)


def _mcp_seed_profile(engine, name: str) -> None:
    """FK on atomic_facts → profiles; same INSERT OR IGNORE convention as
    tests/test_core/test_engine_list_facts.py."""
    engine._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
        (name, name),
    )


def _mcp_store(engine, fact_id: str, content: str,
               profile_id: str | None = None) -> None:
    from superlocalmemory.storage.models import AtomicFact, FactType

    fact = AtomicFact(
        fact_id=fact_id, memory_id="", content=content,
        fact_type=FactType.SEMANTIC, entities=["Probe"], confidence=0.9,
    )
    engine.store_fact_direct(fact, profile_id=profile_id)


class TestMcpListRecent:
    def test_tool_accepts_profile_id(
        self, mcp_server, engine_with_mock_deps,
    ) -> None:
        _mcp_seed_profile(engine_with_mock_deps, "b")
        _mcp_store(
            engine_with_mock_deps, "mcp-b-1", LONG_CONTENT, profile_id="b",
        )
        _mcp_store(
            engine_with_mock_deps, "mcp-act-1", "active profile probe",
        )

        result = mcp_server.call_tool(
            "list_recent", {"limit": 5, "profile_id": "b"},
        )

        assert result["success"] is True
        assert result["results"]
        assert "importance" in result["results"][0]
        # Routing is real: only profile b's facts come back.
        ids = {item["fact_id"] for item in result["results"]}
        assert ids == {"mcp-b-1"}, (
            f"profile_id='b' must list only b's facts, got {ids!r}"
        )
        # The engine's active pointer never moves.
        assert engine_with_mock_deps._profile_id != "b"

    def test_schema_allows_optional_param(self, mcp_server) -> None:
        schema = mcp_server.get_tool_schema("list_recent")
        assert "profile_id" in schema["inputSchema"]["properties"]
        assert "profile_id" not in schema["inputSchema"].get("required", [])

    def test_no_profile_id_legacy(
        self, mcp_server, engine_with_mock_deps,
    ) -> None:
        _mcp_store(
            engine_with_mock_deps, "mcp-leg-1", "legacy active probe",
        )

        result = mcp_server.call_tool("list_recent", {"limit": 5})

        assert result["success"] is True
        ids = {item["fact_id"] for item in result["results"]}
        assert "mcp-leg-1" in ids

    def test_offline_return_echoes_profile(
        self, mcp_server, engine_with_mock_deps,
    ) -> None:
        """Envelope parity with the daemon path: the offline return echoes
        the profile the read was actually served from — the explicit anchor
        when set, the engine's active profile when unset (same resolution as
        the daemon /list route's ``req_profile or engine.profile_id``)."""
        _mcp_seed_profile(engine_with_mock_deps, "b")
        _mcp_store(
            engine_with_mock_deps, "mcp-echo-b", "routed echo probe",
            profile_id="b",
        )

        routed = mcp_server.call_tool(
            "list_recent", {"limit": 5, "profile_id": "b"},
        )

        assert routed["success"] is True
        assert routed["profile"] == "b", routed

        legacy = mcp_server.call_tool("list_recent", {"limit": 5})

        assert legacy["success"] is True
        assert legacy["profile"] == engine_with_mock_deps.profile_id, legacy
        # The echo is real: it names the profile that actually served the
        # read, and the routed call never moved the active pointer.
        assert engine_with_mock_deps.profile_id != "b"

    def test_content_not_truncated_and_fields_complete(
        self, mcp_server, engine_with_mock_deps,
    ) -> None:
        # LONG_CONTENT is well past the pre-fix 120-char MCP truncation
        # boundary, so a truncated result cannot accidentally equal it.
        _mcp_store(engine_with_mock_deps, "mcp-long-1", LONG_CONTENT)

        result = mcp_server.call_tool("list_recent", {"limit": 5})

        assert result["success"] is True
        item = next(
            i for i in result["results"] if i["fact_id"] == "mcp-long-1"
        )
        assert item["content"] == LONG_CONTENT
        assert "importance" in item
        # Pre-existing fields are preserved, not dropped.
        assert item["fact_type"]
        assert item["created_at"]
        assert "session_id" in item

    def test_daemon_path_routes_profile_id(
        self, mcp_server, monkeypatch,
    ) -> None:
        calls: list[tuple] = []

        def _fake_daemon_request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            return {
                "success": True,
                "results": [{
                    "fact_id": "d-1",
                    "content": LONG_CONTENT,
                    "fact_type": "semantic",
                    "created_at": "2026-09-01T00:00:00",
                    "importance": 0.7,
                }],
                "count": 1,
                "profile": "b",
            }

        monkeypatch.setattr(
            "superlocalmemory.cli.daemon.is_daemon_running", lambda: True,
        )
        monkeypatch.setattr(
            "superlocalmemory.cli.daemon.daemon_request",
            _fake_daemon_request,
        )

        result = mcp_server.call_tool(
            "list_recent", {"limit": 5, "profile_id": "b"},
        )

        assert result["success"] is True
        assert result["results"][0]["fact_id"] == "d-1"
        assert result["profile"] == "b"
        assert calls, "daemon-running must route through the daemon /list"
        method, path, _ = calls[0]
        assert method == "GET"
        assert path.startswith("/list?")
        assert "limit=5" in path
        assert "profile_id=b" in path

    def test_daemon_path_omits_unset_profile_id(
        self, mcp_server, monkeypatch,
    ) -> None:
        """An unset anchor keeps the legacy daemon request byte-identical:
        no profile_id key is put on the wire at all."""
        calls: list[tuple] = []

        def _fake_daemon_request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            return {
                "success": True, "results": [], "count": 0,
                "profile": "default",
            }

        monkeypatch.setattr(
            "superlocalmemory.cli.daemon.is_daemon_running", lambda: True,
        )
        monkeypatch.setattr(
            "superlocalmemory.cli.daemon.daemon_request",
            _fake_daemon_request,
        )

        result = mcp_server.call_tool("list_recent", {"limit": 5})

        assert result["success"] is True
        list_calls = [c for c in calls if c[1].startswith("/list")]
        assert len(list_calls) == 1, (
            f"expected exactly one daemon /list request, got {calls!r}"
        )
        method, path, _ = list_calls[0]
        assert method == "GET"
        assert "profile_id" not in path
