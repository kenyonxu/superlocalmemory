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

    def test_content_revision_successor_inherits_tag_and_scope(self, daemon) -> None:
        """Fix round 1, finding 1: the successor carries the curation state.

        A curator fixing content and tagging in one call must not end with
        an untagged live fact: the successor copies scope/shared_with from
        the predecessor row, and provenance_kind travels the same way.
        """
        import json as _json

        client, app = daemon
        client.post("/remember", json={
            "content": (
                "The untagged conveyor inspection rota waits for its "
                "content revision and governance tag."
            ),
            "idempotency_key": "prov-upd-successor-1",
        })
        fid = _newest(client)["fact_id"]

        response = client.patch(f"/api/memories/{fid}", json={
            "content": (
                "The revised conveyor inspection rota carries its "
                "governance tag through the correction lineage."
            ),
            "provenance_kind": "curated",
            "scope": "global",
        })

        assert response.status_code == 202, response.text
        successor = response.json()["successor_fact_id"]
        assert successor and successor != fid
        rows = app.state.engine._db.execute(
            "SELECT scope, shared_with, provenance_kind FROM atomic_facts "
            "WHERE fact_id = ?",
            (successor,),
        )
        assert rows, "the 202 must disclose a persisted successor row"
        assert rows[0]["scope"] == "global"
        assert rows[0]["provenance_kind"] == "curated"
        assert _json.loads(rows[0]["shared_with"] or "[]") == []

    def test_routed_content_correction_rejected_before_any_write(self, daemon) -> None:
        """Fix round 1, finding 2: no partial write on a routed content edit.

        The curation fields of a routed request must not commit when the
        content correction that follows can only resolve against the ACTIVE
        profile — the pre-flight rejects the whole request before any
        durable mutation, so the status never lies about a committed write.
        """
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "Doris keeps the northern depot roster and its review "
                "schedule for profile b."
            ),
            "profile_id": "b",
            "idempotency_key": "prov-upd-preflight-b-1",
        })
        fid = _newest(client, profile_id="b")["fact_id"]
        before = client.get("/status").json()

        response = client.patch(
            f"/api/memories/{fid}?profile_id=b",
            json={
                "content": (
                    "The revised northern depot roster carries a routed "
                    "content correction attempt."
                ),
                "provenance_kind": "curated",
                "scope": "global",
            },
        )

        assert 400 <= response.status_code < 500, response.text
        body = response.json()
        assert body.get("error", {}).get("code") == (
            "routed_content_correction_unsupported"
        )
        # Nothing landed: the addressed fact is byte-identical, and the
        # active-profile pointer never moved.
        fact = _newest(client, profile_id="b")
        assert fact["fact_id"] == fid
        assert fact["provenance_kind"] is None
        assert fact["scope"] == "personal"
        after = client.get("/status").json()
        assert after["profile"] == before["profile"]
        assert after["profile_generation"] == before["profile_generation"]

    def test_clear_tag_with_json_null_literal(self, daemon) -> None:
        """Fix round 1, finding 3b: JSON null clears the tag, like ""."""
        client, _ = daemon
        client.post("/remember", json={
            "content": (
                "The tagged harbor pilot rotation carries a world tag "
                "until a curator clears it with a JSON null."
            ),
            "provenance_kind": "world",
            "idempotency_key": "prov-upd-null-clear-1",
        })
        fid = _newest(client)["fact_id"]

        cleared = client.patch(
            f"/api/memories/{fid}", json={"provenance_kind": None},
        )

        assert cleared.status_code == 200, cleared.text
        assert _newest(client)["provenance_kind"] is None

    def test_migrate_to_shared_with_shared_with(self, daemon) -> None:
        """Fix round 1, finding 3c: shared scope rides the unified PATCH.

        Parity with PATCH /api/memories/{id}/scope: shared stores its
        shared_with as a JSON array, and shared without the list is a 400.
        """
        import json as _json

        client, app = daemon
        client.post("/remember", json={
            "content": (
                "The quartermaster inventory ledger migrates to the "
                "shared team scope through the unified revision route."
            ),
            "idempotency_key": "prov-upd-shared-1",
        })
        fid = _newest(client)["fact_id"]

        migrated = client.patch(f"/api/memories/{fid}", json={
            "scope": "shared", "shared_with": "team-alpha, team-beta",
        })
        assert migrated.status_code == 200, migrated.text

        rows = app.state.engine._db.execute(
            "SELECT scope, shared_with FROM atomic_facts WHERE fact_id = ?",
            (fid,),
        )
        assert rows[0]["scope"] == "shared"
        assert _json.loads(rows[0]["shared_with"]) == ["team-alpha", "team-beta"]

        missing_list = client.patch(
            f"/api/memories/{fid}", json={"scope": "shared"},
        )
        assert missing_list.status_code == 400, missing_list.text


class TestDeleteMemoryProfileRouting:
    """Fix round 1, finding 3a: the routed DELETE at route level.

    Real TestClient daemon traffic (not an MCP wire mock): a routed delete
    removes only the routed profile's fact, and an unknown routed profile
    is the structured 404 envelope, never an implicit creation.
    """

    def test_routed_delete_removes_only_the_routed_profiles_fact(
        self, daemon,
    ) -> None:
        client, app = daemon
        client.post("/remember", json={
            "content": (
                "The routed ledger entry belongs to profile b and its "
                "curators alone until it is deleted."
            ),
            "profile_id": "b",
            "idempotency_key": "prov-del-route-b-1",
        })
        client.post("/remember", json={
            "content": (
                "The active-profile ledger entry outlives a delete that "
                "was routed elsewhere."
            ),
            "idempotency_key": "prov-del-active-1",
        })
        doomed = _newest(client, profile_id="b")["fact_id"]
        survivor = _newest(client)

        response = client.delete(f"/api/memories/{doomed}?profile_id=b")

        assert response.status_code == 200, response.text
        assert response.json()["erasure_verified"] is True
        rows = app.state.engine._db.execute(
            "SELECT 1 FROM atomic_facts WHERE fact_id = ?", (doomed,),
        )
        assert rows == []
        # The active profile's newest fact is untouched by the routed delete.
        after = _newest(client)
        assert after["fact_id"] == survivor["fact_id"]
        listed_b = client.get(
            "/list", params={"profile_id": "b", "limit": 5},
        ).json()["results"]
        assert all(item["fact_id"] != doomed for item in listed_b)

    def test_routed_delete_unknown_profile_is_structured_404(self, daemon) -> None:
        client, _ = daemon

        response = client.delete("/api/memories/whatever?profile_id=ghost")

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_profile"


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

    def test_update_tool_maps_daemon_409_conflict_non_retryable(
        self, monkeypatch,
    ) -> None:
        """Fix round 2: a deterministic 409 is terminal, never retryable.

        update_memory(content=..., profile_id="b") with b != active is
        refused by the daemon's pre-flight with a structured 409. Without
        ``preserve_conflict=True`` daemon_request collapses that answer to
        None and the tool returns the retryable outage envelope — telling
        the caller to retry forever a request that can never succeed.
        """
        import asyncio

        import superlocalmemory.cli.daemon as _d
        from superlocalmemory.cli.daemon import DaemonConflict

        seen: dict = {}

        def _request(method, path, body=None, **kwargs):
            seen.update(kwargs=kwargs, path=path)
            raise DaemonConflict(
                "content corrections run on the daemon's active profile",
                code="routed_content_correction_unsupported",
            )

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: True)
        monkeypatch.setattr(_d, "daemon_request", _request)

        update = _core_tools()["update_memory"]
        result = asyncio.run(update(
            "mcp-fact",
            "revised content the daemon refuses to route",
            profile_id="b",
        ))

        assert result["success"] is False
        # Deterministic conflict: retrying the identical request can never
        # succeed, so the envelope must say so and name the reason.
        assert result["retryable"] is False
        assert result["code"] == "routed_content_correction_unsupported"
        assert "active profile" in result["error"]
        # The conflict must be preserved on the wire, not collapsed to None.
        assert seen["kwargs"].get("preserve_conflict") is True
        assert seen["kwargs"].get("preserve_not_found") is True

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

        assert result["success"] is True
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/api/memories/mcp-fact"


# ---------------------------------------------------------------------------
# Task 4 (spec section 5): read-surface echo + curation-scan filtering.
#
# Echo: every read tool's result items carry ``scope`` and
# ``provenance_kind`` additively — an old consumer that ignores unknown
# keys sees no difference. The four read surfaces: recall (one shared
# serializer chokepoint serves the daemon route, MCP, CLI, and the
# WorkerPool fallback), search, fetch, and list_recent (daemon
# passthrough + the offline item builder).
#
# Scan filtering: /list and MCP list_recent accept ``scope`` /
# ``provenance_kind`` filters; the literal ``provenance_kind=null``
# explicitly selects the not-yet-tagged rows. Out-of-vocabulary values
# are rejected strictly — the scan is the controlled curation face, the
# write path is the forgiving one (spec decision table).
# ---------------------------------------------------------------------------


def _seed_sync(engine, content, *, scope="personal", provenance_kind=None):
    """Seed one fully-enriched fact through the synchronous canonical path.

    Recall and search read materialized artifacts (embeddings, BM25
    tokens); the daemon write path defers those to the background
    materializer, and an echo test that races it flakes. The synchronous
    ``require_complete=True`` entry drives the same canonical_store with
    enrichment finished before the read.
    """
    from superlocalmemory.core.engine_ingestion import (
        canonical_store,
        local_trusted_actor_id,
    )

    return canonical_store(
        engine, content, source_type="python-api",
        trusted_actor_id=local_trusted_actor_id("python-api"),
        scope=scope, provenance_kind=provenance_kind,
        require_complete=True,
    )


def _offline_daemon(monkeypatch):
    """Pin the MCP daemon probes to OFFLINE.

    ``is_daemon_running`` reaches for the resident daemon on its port; on a
    dev box one is running, and an offline-path test must never ask it for
    anything (or leak its answers into an assertion).
    """
    import superlocalmemory.cli.daemon as _d

    monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: False)


def _core_tools_bound(engine):
    """Core MCP tools bound to a real engine (the offline read tools)."""
    from superlocalmemory.mcp.tools_core import register_core_tools

    srv = _ToolCaptureServer()
    register_core_tools(srv, lambda: engine)
    return srv.tools


class TestReadEcho:
    def test_recall_results_carry_scope_and_provenance_kind(
        self, daemon,
    ) -> None:
        client, app = daemon
        _seed_sync(
            app.state.engine,
            "The harbor echo charter is tagged world evidence at "
            "global scope.",
            scope="global", provenance_kind="world",
        )
        listed = client.get("/list", params={"limit": 1}).json()["results"]
        fid = listed[0]["fact_id"]

        response = client.get(
            "/recall", params={"q": "harbor echo charter"},
        )

        assert response.status_code == 200, response.text
        match = next(
            (
                item for item in response.json()["results"]
                if item["fact_id"] == fid
            ),
            None,
        )
        assert match is not None, response.json()["results"]
        assert match["scope"] == "global"
        assert match["provenance_kind"] == "world"

    def test_search_carries_both(self, daemon, monkeypatch) -> None:
        import asyncio

        client, app = daemon
        _seed_sync(
            app.state.engine,
            "The beacon calibration log is curated world evidence.",
            scope="global", provenance_kind="curated",
        )
        _offline_daemon(monkeypatch)
        search = _core_tools_bound(app.state.engine)["search"]

        result = asyncio.run(search("beacon calibration"))

        assert result["success"] is True, result
        assert result["results"], "the seeded fact must be findable"
        assert result["results"][0]["scope"] == "global"
        assert result["results"][0]["provenance_kind"] == "curated"

    def test_fetch_carries_both(self, daemon, monkeypatch) -> None:
        import asyncio

        client, app = daemon
        client.post("/remember", json={
            "content": (
                "The quayside pump rota carries a world tag into the "
                "fetch echo."
            ),
            "scope": "global",
            "provenance_kind": "world",
            "idempotency_key": "prov-echo-fetch-1",
        })
        fid = _newest(client)["fact_id"]
        _offline_daemon(monkeypatch)
        fetch = _core_tools_bound(app.state.engine)["fetch"]

        result = asyncio.run(fetch(fid))

        assert result["success"] is True, result
        assert result["results"][0]["fact_id"] == fid
        assert result["results"][0]["scope"] == "global"
        assert result["results"][0]["provenance_kind"] == "world"

    def test_list_recent_offline_carries_both(
        self, daemon, monkeypatch,
    ) -> None:
        import asyncio

        client, app = daemon
        client.post("/remember", json={
            "content": (
                "The night-watch lantern ledger carries its governance "
                "tag into the list echo."
            ),
            "scope": "global",
            "provenance_kind": "world",
            "idempotency_key": "prov-echo-list-1",
        })
        _offline_daemon(monkeypatch)
        list_recent = _core_tools_bound(app.state.engine)["list_recent"]

        result = asyncio.run(list_recent(limit=5))

        assert result["success"] is True, result
        newest = result["results"][0]
        assert newest["scope"] == "global"
        assert newest["provenance_kind"] == "world"


def _seed_scan_set(client) -> None:
    """The canonical curation-scan fixture: 4 global + 1 personal.

    Global: one world-tagged, one curated-tagged, two untagged. Personal:
    one world-tagged — a fact that must never leak into a scope=global
    scan, but must match a tag-only scan.
    """
    seeds = (
        ("prov-scan-world", "global", "world"),
        ("prov-scan-curated", "global", "curated"),
        ("prov-scan-untagged-a", "global", None),
        ("prov-scan-untagged-b", "global", None),
        ("prov-scan-personal-world", "personal", "world"),
    )
    for key, scope, kind in seeds:
        body = {
            "content": (
                f"The {key} dredging record files under the curation "
                "scan fixture."
            ),
            "scope": scope,
            "idempotency_key": key,
        }
        if kind is not None:
            body["provenance_kind"] = kind
        response = client.post("/remember", json=body)
        assert response.status_code == 200, response.text


class TestCurationScan:
    def test_no_filter_returns_everything(self, daemon) -> None:
        client, _ = daemon
        _seed_scan_set(client)

        response = client.get("/list", params={"limit": 50})

        assert response.status_code == 200, response.text
        # Compatibility anchor: without filter params the scan is the
        # legacy unfiltered list — every seeded row, in one page.
        assert response.json()["count"] >= 5

    def test_filter_scope_global(self, daemon) -> None:
        client, _ = daemon
        _seed_scan_set(client)

        response = client.get("/list", params={"scope": "global"})

        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert all(f["scope"] == "global" for f in results)
        assert len(results) == 4

    def test_filter_provenance_null(self, daemon) -> None:
        client, _ = daemon
        _seed_scan_set(client)

        response = client.get(
            "/list",
            params={"scope": "global", "provenance_kind": "null"},
        )

        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert len(results) == 2
        assert all(f["provenance_kind"] is None for f in results)

    def test_filter_provenance_world(self, daemon) -> None:
        client, _ = daemon
        _seed_scan_set(client)

        response = client.get(
            "/list",
            params={"scope": "global", "provenance_kind": "world"},
        )

        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["provenance_kind"] == "world"

        # A tag-only scan (no scope) crosses scopes: both world facts.
        tag_only = client.get(
            "/list", params={"provenance_kind": "world"},
        )
        assert tag_only.status_code == 200, tag_only.text
        assert len(tag_only.json()["results"]) == 2

    def test_out_of_vocabulary_scan_400(self, daemon) -> None:
        client, _ = daemon

        bad_kind = client.get(
            "/list", params={"provenance_kind": "garbage"},
        )
        assert bad_kind.status_code == 400, bad_kind.text

        bad_scope = client.get("/list", params={"scope": "bogus"})
        assert bad_scope.status_code == 400, bad_scope.text


class TestMcpListRecentScanFilters:
    """list_recent threads the curation-scan filters, wire + offline.

    Wire convention identical to profile_id: the filter params travel ONLY
    when the caller set them, so the legacy request stays byte-identical.
    Out-of-vocabulary is a structured non-retryable failure at the tool
    boundary — the offline path has no daemon 400 to lean on, and a
    misspelled filter must never read as an empty store.
    """

    @staticmethod
    def _capture_daemon(monkeypatch):
        import superlocalmemory.cli.daemon as _d

        captured: dict = {}

        def _request(method, path, body=None, **kwargs):
            captured.update(method=method, path=path)
            return {"success": True, "results": [], "count": 0,
                    "profile": "a"}

        monkeypatch.setattr(_d, "is_daemon_running", lambda *a, **k: True)
        monkeypatch.setattr(_d, "daemon_request", _request)
        return captured

    def test_filters_thread_on_the_wire(self, monkeypatch) -> None:
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        list_recent = _core_tools()["list_recent"]

        result = asyncio.run(
            list_recent(scope="global", provenance_kind="null"),
        )

        assert result["success"] is True, result
        assert captured["method"] == "GET"
        assert "scope=global" in captured["path"]
        assert "provenance_kind=null" in captured["path"]

    def test_legacy_call_has_no_filter_params(self, monkeypatch) -> None:
        import asyncio

        captured = self._capture_daemon(monkeypatch)
        list_recent = _core_tools()["list_recent"]

        result = asyncio.run(list_recent())

        assert result["success"] is True, result
        from superlocalmemory.core.config import CANONICAL_LIST_LIMIT
        assert captured["path"] == f"/list?limit={CANONICAL_LIST_LIMIT}"

    def test_offline_filter_selects_rows(self, daemon, monkeypatch) -> None:
        import asyncio

        client, app = daemon
        _seed_scan_set(client)
        _offline_daemon(monkeypatch)
        list_recent = _core_tools_bound(app.state.engine)["list_recent"]

        result = asyncio.run(
            list_recent(scope="global", provenance_kind="null", limit=50),
        )

        assert result["success"] is True, result
        results = result["results"]
        assert len(results) == 2
        assert all(f["provenance_kind"] is None for f in results)
        assert all(f["scope"] == "global" for f in results)

    def test_out_of_vocabulary_is_structured_failure(
        self, monkeypatch,
    ) -> None:
        import asyncio

        list_recent = _core_tools()["list_recent"]

        for kwargs in (
            {"scope": "bogus"},
            {"provenance_kind": "garbage"},
        ):
            result = asyncio.run(list_recent(**kwargs))
            assert result["success"] is False, (kwargs, result)
            # A deterministic client error: retrying the identical call
            # can never succeed.
            assert result.get("retryable") is False, (kwargs, result)
