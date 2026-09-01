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
) -> None:
    payload: dict = {"content": content}
    if profile_id:
        payload["profile_id"] = profile_id
    if key:
        payload["idempotency_key"] = key
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
