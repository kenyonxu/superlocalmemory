# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Per-request profile routing at the daemon layer (spec section 3/5).

A non-empty ``profile_id`` on POST /remember (body) and GET /recall (query)
is pure routing: the request is served against THAT profile without touching
the ProfileRuntime active pointer or its generation. An unknown profile is
rejected with 404 + ``{"success": false, "error": {"code":
"unknown_profile"}}`` and never implicitly created. An empty/absent
profile_id keeps the legacy path byte-identical, including the stale-client
guard slot, which is unreachable for routed requests.
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

    Mirrors ``test_canonical_remember_route._client``: the daemon-owned
    canonical writer is injected because TestClient does not enter lifespan.
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


def _facts_in(engine, profile_id: str, needle: str = "") -> list[str]:
    rows = engine._db.execute(
        "SELECT fact_id FROM atomic_facts WHERE profile_id = ? AND content LIKE ?",
        (profile_id, f"%{needle}%"),
    )
    return [row["fact_id"] for row in rows]


def _profile_row_count(engine) -> int:
    rows = engine._db.execute("SELECT COUNT(*) AS c FROM profiles")
    return int(dict(rows[0])["c"])


class TestRouting:
    def test_remember_routes_to_explicit_profile(self, daemon) -> None:
        client, app = daemon
        engine = app.state.engine
        response = client.post(
            "/remember",
            json={
                "content": (
                    "Doris owns the release branch schedule and records "
                    "every platform freeze window."
                ),
                "profile_id": "b",
                "idempotency_key": "route-remember-b-1",
            },
        )

        assert response.status_code == 200, response.text
        assert _facts_in(engine, "b", "Doris"), (
            "the routed fact must land in profile b"
        )
        assert _facts_in(engine, "a", "Doris") == []

    def test_recall_routes_to_explicit_profile(self, daemon) -> None:
        client, _ = daemon
        client.post(
            "/remember",
            json={
                "content": (
                    "Doris owns the release branch schedule and records "
                    "every platform freeze window."
                ),
                "profile_id": "b",
                "idempotency_key": "route-recall-b-1",
            },
        )
        client.post(
            "/remember",
            json={
                "content": (
                    "Zebra coordinates the zonal inventory audit and keeps "
                    "the northern warehouse ledger."
                ),
                "profile_id": "a",
                "idempotency_key": "route-recall-a-1",
            },
        )

        hit = client.get(
            "/recall", params={"q": "Doris release branch schedule", "profile_id": "b"},
        )
        assert hit.status_code == 200, hit.text
        assert hit.json()["results"], "recall must hit the routed profile"

        isolated = client.get(
            "/recall", params={"q": "Doris release branch schedule", "profile_id": "a"},
        )
        assert isolated.status_code == 200, isolated.text
        assert isolated.json()["results"] == []

        reverse = client.get(
            "/recall", params={"q": "Zebra inventory audit", "profile_id": "b"},
        )
        assert reverse.json()["results"] == []

    def test_global_pointer_untouched(self, daemon) -> None:
        client, _ = daemon
        before = client.get("/status").json()
        client.post(
            "/remember",
            json={
                "content": (
                    "Quartz buffers the quarterly readiness review for the "
                    "on-call rotation."
                ),
                "profile_id": "b",
                "idempotency_key": "route-pointer-b-1",
            },
        )
        client.get("/recall", params={"q": "Quartz readiness review", "profile_id": "b"})
        after = client.get("/status").json()

        assert after["profile"] == before["profile"]
        assert after["profile_generation"] == before["profile_generation"]

    def test_unknown_profile_rejected(self, daemon) -> None:
        client, app = daemon
        engine = app.state.engine
        profiles_before = _profile_row_count(engine)

        remembered = client.post(
            "/remember",
            json={
                "content": (
                    "Ghost owns no profile and must not be silently created."
                ),
                "profile_id": "ghost",
                "idempotency_key": "route-unknown-1",
            },
        )
        recalled = client.get(
            "/recall", params={"q": "Ghost profile", "profile_id": "ghost"},
        )

        assert remembered.status_code == 404, remembered.text
        body = remembered.json()
        assert body["success"] is False
        assert body["error"]["code"] == "unknown_profile"
        assert body["error"]["profile_id"] == "ghost"

        assert recalled.status_code == 404, recalled.text
        recall_body = recalled.json()
        assert recall_body["success"] is False
        assert recall_body["error"]["code"] == "unknown_profile"

        # No implicit creation, no engine touch.
        assert _profile_row_count(engine) == profiles_before
        assert engine._db.execute(
            "SELECT COUNT(*) AS c FROM atomic_facts WHERE profile_id = 'ghost'"
        )[0]["c"] == 0

    def test_empty_profile_id_is_legacy_path(self, daemon) -> None:
        client, app = daemon
        engine = app.state.engine
        active = client.get("/status").json()["profile"]

        response = client.post(
            "/remember",
            json={
                "content": (
                    "Legacy writes keep landing in the active profile "
                    "exactly as before."
                ),
                "idempotency_key": "route-legacy-1",
            },
        )

        assert response.status_code == 200, response.text
        assert _facts_in(engine, active, "Legacy writes"), (
            "the legacy path must write to the active profile"
        )
        assert _facts_in(engine, "b", "Legacy writes") == []

    def test_active_profile_explicit_is_not_error(self, daemon) -> None:
        client, _ = daemon
        active = client.get("/status").json()["profile"]

        response = client.post(
            "/remember",
            json={
                "content": (
                    "Naming the active profile explicitly is routing, not "
                    "a stale-client conflict."
                ),
                "profile_id": active,
                "idempotency_key": "route-active-1",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["fact_ids"]
