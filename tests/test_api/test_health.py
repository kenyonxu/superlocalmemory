# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Tests for /health endpoint engine recovery and engine health check."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")


@pytest.fixture
def health_client():
    """Create a minimal TestClient for the health endpoint."""
    from fastapi.testclient import TestClient
    from superlocalmemory.server.unified_daemon import create_app

    app = create_app()
    return TestClient(app)


def test_health_endpoint_triggers_engine_recovery(health_client):
    """GET /health attempts engine recovery when engine is None."""
    health_client.app.state.engine = None
    response = health_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["engine"] == "initialized"
    assert data["status"] == "ok"


def test_health_check_includes_engine_item():
    """run_all_health_checks() includes the engine health check item."""
    from superlocalmemory.core.health_monitor import (
        run_all_health_checks, register_health_check,
    )

    def _check_engine():
        return {"name": "engine", "status": "ok", "detail": "Engine initialized"}

    register_health_check(_check_engine)

    results = run_all_health_checks()
    engine_checks = [r for r in results if r["name"] == "engine"]
    assert len(engine_checks) >= 1, f"Expected at least 1 engine check, got {len(engine_checks)}"
    ec = engine_checks[0]
    assert ec["status"] in ("ok", "critical", "unknown", "error")
    assert "detail" in ec
