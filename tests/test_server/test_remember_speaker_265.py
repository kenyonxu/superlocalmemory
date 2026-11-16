"""#265 at the HTTP boundary: a tool call's save is the agent speaking."""

from __future__ import annotations

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


@pytest.fixture
def client_and_seen(engine_with_mock_deps):
    from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime

    engine = engine_with_mock_deps
    with engine._db.raw_connection() as conn:
        for m in (M018_ingestion_operations, M032_write_coordinator_admission,
                  M033_projection_transactions, M034_obligation_integrity,
                  M042_correction_case_ledger):
            m.apply(conn)
    app = create_app()
    app.state.engine = engine
    runtime = CanonicalRememberRuntime.for_engine(engine)
    runtime.start()
    seen: list = []
    original = runtime.remember

    def spy(admission, *a, **kw):
        seen.append(admission)
        return original(admission, *a, **kw)

    runtime.remember = spy
    app.state.canonical_remember_runtime = runtime
    c = TestClient(app)
    c.headers["X-SLM-Daemon-Capability"] = app.state.daemon_descriptor.capability
    c.headers["X-SLM-Target-Instance"] = app.state.daemon_descriptor.instance_id
    try:
        yield c, seen
    finally:
        runtime.stop()


@pytest.mark.parametrize("body, expected", [
    ({"metadata": {"agent_id": "claude-code"}}, ("claude-code", "assistant")),
    ({"metadata": {"agent_id": "mcp_client"}}, ("", "assistant")),
    ({"metadata": {"agent_id": "claude-code"}, "speaker": "Ava"}, ("Ava", "assistant")),
    ({"metadata": {"agent_id": "claude-code"}, "speaker": "user"}, ("", "user")),
    ({}, ("", "user")),
])
def test_remember_speaker(client_and_seen, body, expected) -> None:
    client, seen = client_and_seen
    resp = client.post("/remember", json={"content": "I prefer short answers.", **body})
    assert resp.status_code in (200, 202), resp.text
    assert (seen[-1].speaker, seen[-1].role) == expected
