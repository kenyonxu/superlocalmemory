"""Middleware-approved local writes must not fail a second credential check."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("strict,status", [(False, 200), (True, 403)])
def test_remember_honors_the_same_loopback_policy_as_middleware(
    monkeypatch, engine_with_mock_deps, strict, status
):
    from superlocalmemory.server import unified_daemon as ud
    from superlocalmemory.server import write_identity

    monkeypatch.setattr(write_identity, "_REQUIRE_CREDENTIALS", strict)
    monkeypatch.setattr(write_identity, "_TEST_ISOLATION_ALLOWED", False)
    app = ud.create_app()
    app.state.engine = engine_with_mock_deps
    from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime
    from superlocalmemory.storage.migrations import (
        M018_ingestion_operations,
        M032_write_coordinator_admission,
        M033_projection_transactions,
        M034_obligation_integrity,
    )

    with engine_with_mock_deps._db.raw_connection() as connection:
        for migration in (
            M018_ingestion_operations,
            M032_write_coordinator_admission,
            M033_projection_transactions,
            M034_obligation_integrity,
        ):
            migration.apply(connection)
    runtime = CanonicalRememberRuntime.for_engine(engine_with_mock_deps)
    runtime.start()
    app.state.canonical_remember_runtime = runtime
    client = TestClient(app, base_url="http://127.0.0.1:19781", client=("127.0.0.1", 51000))
    try:
        response = client.post("/remember", json={"content": "local note"})
    finally:
        runtime.stop()
    assert response.status_code == status, response.text
    if strict:
        assert "install token" in response.text


def test_cross_origin_uncredentialed_write_stays_refused(monkeypatch):
    from superlocalmemory.server import unified_daemon as ud
    from superlocalmemory.server import write_identity

    monkeypatch.setattr(write_identity, "_REQUIRE_CREDENTIALS", False)
    app = ud.create_app()
    response = TestClient(app, base_url="http://127.0.0.1:19781", client=("127.0.0.1", 51000)).post(
        "/observe", json={"content": "local note"}, headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 403
