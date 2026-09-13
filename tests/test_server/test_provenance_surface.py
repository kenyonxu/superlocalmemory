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
# The revision surface (spec section 6, Task 3): PATCH /api/memories/{id}
#
# Path split (controller ruling): a body WITH content keeps the existing
# correction chain (predecessor/successor fact_id, review-gated). A body
# with ONLY scope/provenance_kind updates the addressed row IN PLACE — the
# fact_id never changes for a curation action, or deepmaid's historical
# fact_id references break. The revision surface validates STRICTLY
# (out-of-vocabulary → 400): curation is a governance operation, so it is
# held to a tighter contract than the lenient write path above.
# ---------------------------------------------------------------------------


class TestUpdateMemoryProvenance:
    def test_migrate_scope_and_tag_in_place(self, daemon) -> None:
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "Rowan curates the turbine winter rota and keeps the "
                "maintenance ledger current."
            ),
            "scope": "personal",
            "idempotency_key": "prov-upd-inplace-1",
        })
        fid = _newest(client)["fact_id"]
        response = client.patch(f"/api/memories/{fid}", json={
            "scope": "global", "provenance_kind": "curated",
        })

        assert response.status_code == 200, response.text
        fact = _newest(client)
        # In-place: the addressed fact_id, content untouched.
        assert fact["fact_id"] == fid
        assert fact["scope"] == "global"
        assert fact["provenance_kind"] == "curated"
        assert fact["content"].startswith("Rowan curates")

        # Strict curation vocabulary: out-of-vocabulary is a 400 here, not
        # the silent null the lenient write path files (ruling: the
        # revision face is the curation operation surface).
        bad_tag = client.patch(
            f"/api/memories/{fid}", json={"provenance_kind": "garbage"},
        )
        assert bad_tag.status_code == 400, bad_tag.text
        bad_scope = client.patch(
            f"/api/memories/{fid}", json={"scope": "bogus"},
        )
        assert bad_scope.status_code == 400, bad_scope.text

    def test_content_optional(self, daemon) -> None:
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "The original release-window decision remains worded "
                "exactly as filed."
            ),
            "idempotency_key": "prov-upd-keep-1",
        })
        fid = _newest(client)["fact_id"]
        response = client.patch(
            f"/api/memories/{fid}", json={"provenance_kind": "world"},
        )

        assert response.status_code == 200, response.text
        assert _newest(client)["content"].startswith("The original release-window")

    def test_clear_tag_with_null(self, daemon) -> None:
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "The tagged northern convoy schedule carries its governance "
                "mark from the write path."
            ),
            "provenance_kind": "world",
            "idempotency_key": "prov-upd-clear-1",
        })
        fid = _newest(client)["fact_id"]
        cleared = client.patch(
            f"/api/memories/{fid}", json={"provenance_kind": ""},
        )

        assert cleared.status_code == 200, cleared.text
        assert _newest(client)["provenance_kind"] is None

    def test_profile_routing(self, daemon) -> None:
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "Doris owns the platform release calendar and files every "
                "freeze window."
            ),
            "profile_id": "b",
            "idempotency_key": "prov-upd-route-b-1",
        })
        fid = _newest(client, profile_id="b")["fact_id"]
        response = client.patch(
            f"/api/memories/{fid}?profile_id=b",
            json={"provenance_kind": "curated"},
        )

        assert response.status_code == 200, response.text
        before = client.get("/status").json()
        fact = _newest(client, profile_id="b")
        assert fact["provenance_kind"] == "curated"
        after = client.get("/status").json()
        # Pure routing: the active-profile pointer never moves.
        assert after["profile"] == before["profile"]
        assert after["profile_generation"] == before["profile_generation"]

    def test_legacy_active_profile_constraint_without_param(self, daemon) -> None:
        client, _ = daemon
        # No profile_id on a fact owned by another profile: the existing
        # rejection semantics hold, and the other profile's row is untouched.
        client.post("/remember", json={
            "content": (
                "The foreign profile ledger entry belongs to profile b "
                "and its curators alone."
            ),
            "profile_id": "b",
            "idempotency_key": "prov-upd-legacy-b-1",
        })
        fid = _newest(client, profile_id="b")["fact_id"]

        response = client.patch(
            f"/api/memories/{fid}", json={"provenance_kind": "curated"},
        )

        assert response.status_code == 404, response.text
        assert _newest(client, profile_id="b")["provenance_kind"] is None


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


class TestMcpUpdateDeleteProfileRouting:
    """update_memory/delete_memory thread profile_id + curation params.

    Wire convention identical to remember (Task 1): the anchor and the
    curation keys travel ONLY when the caller set them, so the legacy call
    stays byte-identical — pinned separately by
    test_mcp_mutations_use_profile_leased_daemon_routes.
    """

    @staticmethod
    def _capture_daemon(monkeypatch):
        import superlocalmemory.cli.daemon as _d

        captured: dict = {}

        def _request(method, path, body=None, **kwargs):
            captured.update(method=method, path=path, body=body)
            return {"success": True, "fact_id": "mcp-fact"}

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: True)
        monkeypatch.setattr(_d, "daemon_request", _request)
        return captured

    def test_update_tool_threads_curation_and_profile(self, monkeypatch) -> None:
        """A curation-only update reaches the PATCH body + profile query."""
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        update = _core_tools()["update_memory"]
        result = asyncio.run(update(
            "mcp-fact",
            provenance_kind="curated",
            scope="global",
            profile_id="b",
        ))

        assert result["success"] is True, result
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/api/memories/mcp-fact?profile_id=b"
        assert captured["body"]["provenance_kind"] == "curated"
        assert captured["body"]["scope"] == "global"
        # content is optional now: absent, not empty-string, on the wire.
        assert "content" not in captured["body"]

    def test_update_tool_legacy_call_shape_unchanged(self, monkeypatch) -> None:
        """content-only call: no query string, body is the legacy shape."""
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        update = _core_tools()["update_memory"]
        result = asyncio.run(update("mcp-fact", "new text", "agent-a"))

        assert result["success"] is True, result
        assert captured["method"] == "PATCH"
        assert captured["path"] == "/api/memories/mcp-fact"
        assert captured["body"] == {"content": "new text"}

    def test_delete_tool_threads_profile(self, monkeypatch) -> None:
        """delete_memory(profile_id="b") routes the DELETE to profile b."""
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        delete = _core_tools()["delete_memory"]
        result = asyncio.run(delete("mcp-fact", profile_id="b"))

        assert result["success"] is True, result
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/api/memories/mcp-fact?profile_id=b"

    def test_delete_tool_legacy_call_shape_unchanged(self, monkeypatch) -> None:
        """No profile_id: the DELETE URL is the legacy path, unmodified."""
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        delete = _core_tools()["delete_memory"]
        result = asyncio.run(delete("mcp-fact", "agent-a"))

        assert result["success"] is True, result
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/api/memories/mcp-fact"
