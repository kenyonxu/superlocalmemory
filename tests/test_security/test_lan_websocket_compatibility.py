"""Configured LAN browsers retain live updates without granting foreign origins."""
# ruff: noqa: F811 -- imported pytest fixture is requested as a parameter

import secrets

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.test_server.test_rbac_company_mode_credential import (
    _company_mode,
    app_and_engine,  # noqa: F401
)


@pytest.fixture
def lan_browser(app_and_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("SLM_REMOTE", "1")
    monkeypatch.setenv("SLM_ALLOWED_HOSTS", "slm.lan")
    monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "192.168.50.144:*,slm.lan:*")
    key = secrets.token_urlsafe(32)
    (tmp_path / "api_key").write_text(key, encoding="utf-8")
    # The socket peer is deliberately outside the allowlist: it must present
    # a valid network credential, independently of the browser's origin.
    return app_and_engine, key


@pytest.mark.parametrize("host,network", [
    ("192.168.50.144", None), ("slm.lan", None),
    ("192.168.50.144", "192.168.50.0/24"), ("192.168.50.144", "192.168.50.*"),
    ("192.168.50.144", "*"), ("slm.lan", "*"),
])
def test_configured_lan_browser_receives_broadcast(lan_browser, monkeypatch, host, network):
    from superlocalmemory.server.routes.ws import manager

    app, key = lan_browser
    if network:
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", network)
    origin = f"http://{host}:{app.state.daemon_descriptor.port}"
    # Exact/subnet/prefix cases require the key independently of the origin.
    # Global '*' is the documented opt-in to trusting all network peers.
    client = TestClient(app, base_url=origin, client=("192.168.51.99", 51000))
    headers = {"Origin": origin, "X-SLM-API-Key": key}
    assert client.get("/api/memories", headers=headers).status_code == 200
    # Starlette joins relative WS paths to ws://testserver, ignoring base_url.
    url = origin.replace("http://", "ws://", 1) + "/ws/updates"
    with client.websocket_connect(url, headers=headers) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.portal.call(manager.broadcast, {"type": "lan_live_update"})
        assert ws.receive_json()["type"] == "lan_live_update"


@pytest.mark.parametrize("case", [
    "foreign", "other_port", "remote_off", "malformed", "wildcard", "cidr",
    "literal_wildcard", "literal_suffix_wildcard",
    "prefix_outside", "prefix_domain",
    "wildcard_same_host_other_port", "wildcard_bad_host",
])
def test_network_key_does_not_authorize_unowned_browser(lan_browser, monkeypatch, case):
    app, key = lan_browser
    port = app.state.daemon_descriptor.port
    origin = f"http://slm.lan:{port}"
    if case == "foreign":
        origin = f"https://attacker.example:{port}"
    elif case == "other_port":
        origin = f"http://slm.lan:{port + 1 if port < 65535 else port - 1}"
    elif case == "remote_off":
        monkeypatch.delenv("SLM_REMOTE")
    elif case == "malformed":
        origin += "/not-an-origin"
    elif case == "wildcard":
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "*")
    elif case == "cidr":
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "192.168.51.0/24")
        origin = f"http://192.168.50.144:{port}"
    elif case in ("literal_wildcard", "literal_suffix_wildcard"):
        host = "*" if case == "literal_wildcard" else "*.office.lan"
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", host)
        origin = f"http://{host}:{port}"
    elif case in ("prefix_outside", "prefix_domain"):
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "192.168.50.*")
        host = "192.168.51.144" if case == "prefix_outside" else "192.168.50.evil.example"
        origin = f"http://{host}:{port}"
    elif case in ("wildcard_same_host_other_port", "wildcard_bad_host"):
        monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "*")
        if case == "wildcard_same_host_other_port":
            other = port + 1 if port < 65535 else port - 1
            origin = f"http://slm.lan:{other}"
    client = TestClient(app, client=("192.168.50.99", 51000))
    headers = {"Origin": origin, "X-SLM-API-Key": key}
    if case == "wildcard_same_host_other_port":
        headers["Host"] = f"slm.lan:{other}"
    elif case == "wildcard_bad_host":
        headers["Host"] = f"user@slm.lan:{port}"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates", headers=headers):
            pytest.fail("unowned LAN browser was accepted")
    assert exc.value.code == 1008


@pytest.mark.parametrize("credential", ["absent", "invalid", "no_session", "no_read"])
def test_lan_socket_keeps_network_and_rbac_gates(lan_browser, credential):
    app, key = lan_browser
    client = TestClient(app, client=("192.168.50.99", 51000))
    headers = {"Origin": f"http://slm.lan:{app.state.daemon_descriptor.port}"}
    if credential == "invalid":
        headers["X-SLM-API-Key"] = "invalid-network-credential"
    elif credential in ("no_session", "no_read"):
        headers["X-SLM-API-Key"] = key
        _company_mode(app)
        if credential == "no_read":
            rbac = app.state.rbac
            rbac.create_user("unassigned", "test-password-1234")
            uid = next(u["user_id"] for u in rbac.list_users()
                       if u["username"] == "unassigned")
            headers["X-SLM-User-Session"] = rbac.create_session(uid)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates", headers=headers):
            pytest.fail("unauthorized LAN socket was accepted")
    assert exc.value.code == 4001


@pytest.mark.parametrize("credential", ["absent", "invalid", "no_read", "admin"])
def test_global_network_trust_still_requires_company_read(lan_browser, monkeypatch, credential):
    app, _ = lan_browser
    monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "*")
    _company_mode(app)
    rbac = app.state.rbac
    origin = f"http://slm.lan:{app.state.daemon_descriptor.port}"
    url = origin.replace("http://", "ws://", 1) + "/ws/updates"
    client = TestClient(app, client=("192.168.51.99", 51000))
    headers = {"Origin": origin}
    if credential == "invalid":
        headers["X-SLM-User-Session"] = "invalid-session"
    elif credential == "no_read":
        rbac.create_user("unassigned", "test-password-1234")
        uid = next(u["user_id"] for u in rbac.list_users() if u["username"] == "unassigned")
        headers["X-SLM-User-Session"] = rbac.create_session(uid)
    elif credential == "admin":
        uid = next(u["user_id"] for u in rbac.list_users() if u["username"] == "admin1")
        headers["X-SLM-User-Session"] = rbac.create_session(uid)
        with client.websocket_connect(url, headers=headers) as ws:
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
        return
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, headers=headers):
            pytest.fail("network wildcard bypassed company READ permission")
    assert exc.value.code == 4001
