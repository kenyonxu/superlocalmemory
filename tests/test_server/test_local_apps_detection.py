"""The Connected apps pane reports every configured host and what each one can use."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.infra import mcp_processes
from superlocalmemory.server.routes import local_apps


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLM_DAEMON_PORT", "19782")
    from superlocalmemory.core.security_primitives import ensure_install_token

    token = ensure_install_token()
    app = FastAPI()
    app.include_router(local_apps.router)
    return TestClient(app, base_url="http://127.0.0.1:19782", client=("127.0.0.1", 51000),
                      headers={"X-Install-Token": token})


def _live(host, tools, version="9.9.9"):
    return {"session_id": f"s_{host}", "host": host, "name": mcp_processes.HOST_NAMES[host],
            "code_version": version, "stale_version": False, "version_reported": True,
            "connection_evidence": "initialized-session", "tools": tools, "pid": 1}


def test_configured_hosts_are_reported_with_live_state(client, monkeypatch):
    from superlocalmemory.mcp.profiles import _PROFILE_DEFINITIONS

    monkeypatch.setattr("superlocalmemory.cli.host_upgrades._detected_hosts",
                        lambda home=None: ["claude-code", "codex", "cursor"])
    live = [_live("codex", sorted(_PROFILE_DEFINITIONS["core"]))]
    monkeypatch.setattr(mcp_processes, "snapshot", lambda root: {
        "apps": live, "hosts": [], "count": 1, "confirmed_count": 1, "stale_count": 0,
        "unreported_count": 0, "installed_version": "9.9.9"})
    body = client.get("/api/v3/local-apps").json()
    by_host = {row["id"]: row for row in body["configured_hosts"]}
    assert set(by_host) == {"claude-code", "codex", "cursor"}
    assert by_host["codex"]["live"] == 1 and by_host["codex"]["name"] == "Codex"
    assert by_host["claude-code"]["live"] == 0
    assert body["apps"][0]["tools_profile"] == "core"


def test_connect_choices_mark_configured_and_live_hosts(client, monkeypatch):
    monkeypatch.setattr("superlocalmemory.cli.host_upgrades._detected_hosts",
                        lambda home=None: ["claude-desktop", "codex"])
    monkeypatch.setattr(mcp_processes, "snapshot", lambda root: {
        "apps": [_live("codex", ["x"])], "hosts": [], "count": 1, "confirmed_count": 1,
        "stale_count": 0, "unreported_count": 0, "installed_version": "9.9.9"})
    hosts = {h["id"]: h for h in client.get("/api/v3/local-apps").json()["available_hosts"]}
    assert hosts["codex"]["connected"] is True and hosts["codex"]["configured"] is True
    assert hosts["claude-desktop"]["connected"] is False and hosts["claude-desktop"]["configured"] is True
    assert hosts["cursor"]["configured"] is False


def test_detection_failure_never_fails_the_status(client, monkeypatch):
    def boom(home=None):
        raise RuntimeError("unreadable")

    monkeypatch.setattr("superlocalmemory.cli.host_upgrades._detected_hosts", boom)
    monkeypatch.setattr("superlocalmemory.infra.mcp_legacy._processes", lambda: [])
    result = client.get("/api/v3/local-apps")
    assert result.status_code == 200 and result.json()["configured_hosts"] == []
