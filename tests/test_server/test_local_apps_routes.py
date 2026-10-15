"""The dashboard can connect a host and run diagnostics without a terminal."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import local_apps


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLM_DAEMON_PORT", "19781")
    from superlocalmemory.core.security_primitives import ensure_install_token

    token = ensure_install_token()
    app = FastAPI()
    app.include_router(local_apps.router)
    return TestClient(
        app,
        base_url="http://127.0.0.1:19781",
        client=("127.0.0.1", 51000),
        headers={"X-Install-Token": token},
    )


def test_status_contains_live_count_and_profile_choices(client):
    result = client.get("/api/v3/local-apps")
    assert result.status_code == 200
    assert result.json()["count"] == 0
    assert {p["id"] for p in result.json()["profiles"]} >= {"core", "code", "full"}
    assert "codex" in {h["id"] for h in result.json()["available_hosts"]}


def test_connect_uses_existing_writer_with_selected_tools_and_port(client, monkeypatch):
    from superlocalmemory.hooks import portable_kit

    seen = []
    monkeypatch.setattr(
        portable_kit,
        "connect_ide",
        lambda host, **kw: seen.append((host, kw)) or {"mcp_config": "wrote", "error": None},
    )
    result = client.post(
        "/api/v3/local-apps/connect", json={"host": "codex", "tools_profile": "core"}
    )
    assert result.status_code == 200
    assert seen[0][0] == "codex"
    assert seen[0][1]["profile"] == "core"
    assert seen[0][1]["daemon_port"] == 19781
    assert result.json()["connected"] is False  # config write is not live evidence


def test_unknown_host_or_profile_never_writes(client):
    assert (
        client.post(
            "/api/v3/local-apps/connect", json={"host": "unknown", "tools_profile": "code"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v3/local-apps/connect", json={"host": "codex", "tools_profile": "unknown"}
        ).status_code
        == 400
    )


def test_diagnostics_and_repair_are_clickable_actions(client, monkeypatch):
    action = AsyncMock(return_value={"success": True, "checks": [], "summary": {"passed": 2}})
    monkeypatch.setattr(local_apps, "run_doctor", action)
    assert client.post("/api/daemon/doctor").json()["success"] is True
    assert client.post("/api/daemon/repair").json()["success"] is True
    assert action.call_args_list[0].kwargs == {"repair": False}
    assert action.call_args_list[1].kwargs == {"repair": True}


def test_remote_caller_cannot_read_presence_or_request_actions(client):
    other = TestClient(client.app, base_url="http://127.0.0.1:19781", client=("192.0.2.5", 51000))
    assert other.get("/api/v3/local-apps").status_code == 403
    assert other.post("/api/daemon/repair").status_code == 403


def test_reconnect_does_not_claim_an_absent_session(client):
    assert client.post("/api/v3/local-apps/absent/reconnect").status_code == 404
