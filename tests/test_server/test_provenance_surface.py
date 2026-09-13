# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Write surface for provenance_kind (spec section 4).

``provenance_kind`` is an optional controlled-vocabulary tag on the remember
write path: MCP ``remember(..., provenance_kind="world")`` → daemon
``/remember`` body → engine store chain → ``atomic_facts.provenance_kind``.
The write path is lenient by design (spec decision table): the empty
default means untagged (None) and keeps legacy calls byte-identical, and an
out-of-vocabulary value files itself as None instead of failing the write —
"unknown" and "not yet curated" share the safest default. Per-request
profile routing is orthogonal and must keep working: one write can carry
both a routing anchor and a governance tag.
"""

from __future__ import annotations

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


@contextmanager
def _daemon(engine, profiles=("a", "b")):
    """TestClient daemon with pre-created profiles, per tests/test_server convention.

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


def _newest(client, profile_id: str = "") -> dict:
    """The newest fact on /list, optionally routed to one profile."""
    params = {"limit": 1}
    if profile_id:
        params["profile_id"] = profile_id
    listed = client.get("/list", params=params)
    assert listed.status_code == 200, listed.text
    results = listed.json()["results"]
    assert results, "the write above must have produced a listable fact"
    return results[0]


class TestRememberProvenance:
    def test_writes_with_provenance_kind(self, daemon) -> None:
        client, _ = daemon
        response = client.post(
            "/remember",
            json={
                "content": (
                    "Willow keeps the western windfarm rota and files the "
                    "turbine maintenance ledger."
                ),
                "provenance_kind": "world",
                "idempotency_key": "prov-write-world-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["fact_ids"]
        # The tag survives admission, the journal, and the coordinator
        # projection into the durable row the read surface lists.
        assert _newest(client)["provenance_kind"] == "world"

    def test_out_of_vocabulary_becomes_null(self, daemon) -> None:
        client, _ = daemon
        response = client.post(
            "/remember",
            json={
                "content": (
                    "Garbage tag Gibbons guards no governed vocabulary "
                    "entry at all."
                ),
                "provenance_kind": "garbage",
                "idempotency_key": "prov-write-garbage-1",
            },
        )

        assert response.status_code == 200, response.text
        # Write-path leniency (spec decision table): unknown = unfiled, the
        # write is never rejected, the tag lands as NULL.
        assert _newest(client)["provenance_kind"] is None

    def test_no_param_defaults_null(self, daemon) -> None:
        client, _ = daemon
        response = client.post(
            "/remember",
            json={
                "content": (
                    "Plain Petal carries no governance tag on the legacy "
                    "write shape."
                ),
                "idempotency_key": "prov-write-plain-1",
            },
        )

        assert response.status_code == 200, response.text
        assert _newest(client)["provenance_kind"] is None

    def test_profile_routing_still_works(self, daemon) -> None:
        client, _ = daemon
        response = client.post(
            "/remember",
            json={
                "content": (
                    "Doris owns the release branch schedule and records "
                    "every platform freeze window."
                ),
                "profile_id": "b",
                "provenance_kind": "world",
                "idempotency_key": "prov-write-route-b-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["profile"] == "b"
        assert _newest(client, profile_id="b")["provenance_kind"] == "world"


# ---------------------------------------------------------------------------
# The MCP tool surface (spec section 4)
#
# remember accepts an optional ``provenance_kind`` and threads it into the
# daemon body. Empty keeps the legacy call byte-identical: the parameter
# must not appear in the daemon request at all when it was not set — the
# same convention profile_id established.
# ---------------------------------------------------------------------------

class _ToolCaptureServer:
    """Minimal @server.tool() capture, matching the tests/test_mcp convention."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def _core_tools() -> dict[str, object]:
    from unittest.mock import MagicMock

    from superlocalmemory.mcp.tools_core import register_core_tools

    srv = _ToolCaptureServer()
    register_core_tools(srv, MagicMock())
    return srv.tools


class TestMcpProvenanceSurface:
    def test_remember_tool_puts_tag_on_the_wire(self, monkeypatch) -> None:
        """remember(provenance_kind="world") carries the tag in the body."""
        import asyncio

        import superlocalmemory.cli.daemon as _d

        captured: dict = {}

        def _request(method, path, body=None, **kwargs):
            captured.update(method=method, path=path, body=body)
            return {"ok": True, "fact_ids": ["mcp-fact"], "count": 1,
                    "status": "stored"}

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: True)
        monkeypatch.setattr(_d, "daemon_request", _request)

        remember = _core_tools()["remember"]
        result = asyncio.run(remember("mcp fact", provenance_kind="world"))

        assert result["success"] is True, result
        assert captured["method"] == "POST"
        assert captured["path"] == "/remember"
        assert captured["body"]["provenance_kind"] == "world"

    def test_remember_tool_legacy_call_has_no_tag_key(self, monkeypatch) -> None:
        """No provenance_kind → the daemon body is the legacy shape, key absent."""
        import asyncio

        import superlocalmemory.cli.daemon as _d

        captured: dict = {}

        def _request(method, path, body=None, **kwargs):
            captured.update(method=method, path=path, body=body)
            return {"ok": True, "fact_ids": ["mcp-fact"], "count": 1,
                    "status": "stored"}

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: True)
        monkeypatch.setattr(_d, "daemon_request", _request)

        remember = _core_tools()["remember"]
        result = asyncio.run(remember("mcp fact"))

        assert result["success"] is True, result
        assert "provenance_kind" not in captured["body"]

    def test_remember_tool_fallback_threads_tag(
        self, monkeypatch,
    ) -> None:
        """The pool.store fallback carries the tag in worker metadata."""
        import asyncio
        from unittest.mock import MagicMock

        import superlocalmemory.cli.daemon as _d

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: False)
        pool = MagicMock()
        pool.store.return_value = {
            "ok": True, "fact_ids": ["mcp-fact"], "count": 1,
            "operation_id": "op-mcp", "pending_id": None,
            "materialization_state": "complete",
        }

        from unittest.mock import patch

        remember = _core_tools()["remember"]
        with patch(
            "superlocalmemory.mcp._daemon_proxy.choose_pool", return_value=pool,
        ):
            result = asyncio.run(
                remember("mcp fact", provenance_kind="world"),
            )

        assert result["success"] is True, result
        pool.store.assert_called_once()
        assert pool.store.call_args.args[1]["provenance_kind"] == "world"

    def test_daemon_pool_proxy_forwards_tag_in_store_body(
        self, monkeypatch,
    ) -> None:
        """DaemonPoolProxy serializes the tag into the POST /remember body.

        Same executable passthrough check as the profile_id anchor: without
        the body key the MCP tool's tag cannot reach the daemon's validation
        at all, and a tag silently filed as metadata would never reach the
        fact row.
        """
        from superlocalmemory.mcp._daemon_proxy import DaemonPoolProxy

        captured: dict = {}

        def _request(method, path, body=None, **kwargs):
            captured.update(method=method, path=path, body=body)
            return {"ok": True, "fact_ids": ["mcp-fact"], "count": 1,
                    "status": "stored"}

        monkeypatch.setattr(
            "superlocalmemory.cli.daemon.daemon_request", _request,
        )

        proxy = DaemonPoolProxy(port=9999)
        assert proxy.store(
            "proxy fact", {"provenance_kind": "world"},
        )["ok"] is True
        assert captured["body"]["provenance_kind"] == "world"

        # Legacy shape: an unset tag never appears on the wire.
        assert proxy.store("proxy fact", {})["ok"] is True
        assert "provenance_kind" not in captured["body"]
