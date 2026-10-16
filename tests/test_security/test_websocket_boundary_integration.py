"""The production WebSocket route enforces HTTP browser and account trust."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.test_security.test_bound_origin_integration import browser_app  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.mark.parametrize("origin", [
    "http://localhost:8765", "http://127.0.0.1:8767",
    "http://localhost:8417", "https://evil.example",
    "http://localhost.evil.example", "null",
    "same_port_other_loopback", "same_port_https",
])
def test_company_cookie_does_not_authorize_foreign_ws(browser_app, origin):
    app, client = browser_app
    if origin == "same_port_other_loopback":
        origin = f"http://127.0.0.2:{app.state.daemon_descriptor.port}"
    elif origin == "same_port_https":
        origin = f"https://localhost:{app.state.daemon_descriptor.port}"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates", headers={"Origin": origin}):
            pytest.fail("foreign browser origin was accepted")
    assert exc.value.code == 1008


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_company_same_origin_ws_and_http_both_work(browser_app, host):
    app, client = browser_app
    origin = f"http://{host}:{app.state.daemon_descriptor.port}"
    assert client.get("/api/memories", headers={"Origin": origin}).status_code == 200
    with client.websocket_connect("/ws/updates", headers={"Origin": origin}) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


@pytest.mark.parametrize("credential", ["absent", "invalid", "no_read"])
def test_company_ws_requires_session_and_workspace_read(browser_app, credential):
    app, client = browser_app
    client.cookies.clear()
    headers = {"Origin": f"http://127.0.0.1:{app.state.daemon_descriptor.port}"}
    if credential == "invalid":
        headers["X-SLM-User-Session"] = "invalid-test-session"
    elif credential == "no_read":
        rbac = app.state.rbac
        rbac.create_user("unassigned", "test-password-1234")
        uid = next(u["user_id"] for u in rbac.list_users() if u["username"] == "unassigned")
        headers["X-SLM-User-Session"] = rbac.create_session(uid)
    assert client.get("/api/memories", headers=headers).status_code in (401, 403)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates", headers=headers):
            pytest.fail("unauthorized company socket accepted")
    assert exc.value.code == 4001


def test_remote_ws_requires_network_auth_even_without_origin(browser_app):
    app, _ = browser_app
    client = TestClient(app, client=("203.0.113.10", 51000))
    assert client.get("/api/memories").status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates"):
            pytest.fail("anonymous network socket accepted")
    assert exc.value.code == 4001


def test_personal_nonbrowser_socket_works(app_and_engine):
    client = TestClient(app_and_engine, client=("127.0.0.1", 51000))
    with client.websocket_connect("/ws/updates") as ws:
        assert ws.receive_json()["type"] == "connected"


def test_origin_helper_failure_cannot_open_socket(browser_app, monkeypatch):
    from superlocalmemory.server import origin

    def unavailable(*args, **kwargs):
        raise RuntimeError("origin helper unavailable")

    monkeypatch.setattr(origin, "daemon_browser_origins", unavailable)
    _, client = browser_app
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/updates", headers={"Origin": "http://localhost.evil.example"}):
            pytest.fail("failed origin check accepted")
    assert exc.value.code == 1008
