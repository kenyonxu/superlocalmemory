"""Every sensitive route rejects a Host the loopback listener does not own."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import re

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.test_security.test_proxy_boundary_integration import proxy_app  # noqa: F401
from tests.test_server.test_company_mode_get_routes import _walk
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.mark.parametrize("host", [
    "attacker.example", "localhost.attacker.example", "192.0.2.10", "[2001:db8::1]",
])
def test_all_sensitive_routes_reject_nonloopback_host(proxy_app, host):
    from superlocalmemory.server.access_gate import path_policy

    app, client, sent = proxy_app
    # Probe a finite route first: a regression must not enter an open SSE
    # stream while walking the entire route table.
    assert client.get("/internal/token", headers={"Host": host}).status_code == 421
    refused = []
    count = 0
    for route, path in _walk(app.routes):
        if path_policy(path) == "public":
            continue
        for method in getattr(route, "methods", None) or ():
            if method in ("HEAD", "OPTIONS"):
                continue
            concrete = re.sub(r"\{[^}]*\}", "fixture", path)
            response = client.request(method, concrete, headers={"Host": host})
            count += 1
            if response.status_code != 421:
                refused.append((method, path, response.status_code))
    assert count > 100
    assert not refused
    assert not sent
    assert client.get("/mcp", headers={"Host": host}).status_code == 421
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"ws://127.0.0.1:{app.state.daemon_descriptor.port}/ws/updates",
            headers={"Host": host},
        ):
            pytest.fail("foreign Host opened a socket")
    assert exc.value.code == 1008


def test_loopback_bind_cannot_be_overridden_by_host_wildcard(proxy_app, monkeypatch):
    monkeypatch.setenv("SLM_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "*")
    _, client, _ = proxy_app
    assert client.get("/internal/token", headers={"Host": "attacker.example"}).status_code == 421
    assert client.get("/api/memories", headers={"Host": "127.0.0.1"}).status_code == 200
