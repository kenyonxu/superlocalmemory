"""The IPv6 dashboard owns only the browser origin of its real listener."""
# ruff: noqa: F811 -- imported pytest fixture is requested as a parameter

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from tests.test_security.test_configured_proxy_host import running_backend
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def test_real_ipv6_browser_gets_live_updates_at_owned_port(monkeypatch, request):
    from superlocalmemory.core.security_primitives import ensure_install_token
    from superlocalmemory.server.routes.ws import manager

    listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener.bind(("::1", 0))
    port = listener.getsockname()[1]
    assert port not in (8765, 8767)
    monkeypatch.setenv("SLM_DAEMON_PORT", str(port))
    app = request.getfixturevalue("app_and_engine")

    @asynccontextmanager
    async def lifespan(_):
        app.state.test_loop = asyncio.get_running_loop()
        yield

    app.router.lifespan_context = lifespan
    ensure_install_token()
    origin = f"http://[::1]:{port}"
    url = f"ws://[::1]:{port}/ws/updates"
    with running_backend(app, listener):
        with httpx.Client(trust_env=False) as client:
            assert client.get(origin + "/").status_code == 200
            token = client.get(origin + "/internal/token", headers={"Origin": origin})
            assert token.status_code == 200
            assert "access-control-allow-origin" not in token.headers
        with connect(url, origin=origin, proxy=None, open_timeout=4) as ws:
            assert json.loads(ws.recv(timeout=4))["type"] == "connected"
            ws.send(json.dumps({"type": "ping"}))
            assert json.loads(ws.recv(timeout=4))["type"] == "pong"
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({"type": "ipv6_live_update"}), app.state.test_loop,
            ).result(4)
            assert json.loads(ws.recv(timeout=4))["type"] == "ipv6_live_update"
        other_port = port + 1 if port < 65535 else port - 1
        with pytest.raises(InvalidStatus) as exc:
            with connect(url, origin=f"http://[::1]:{other_port}", proxy=None, open_timeout=4):
                pytest.fail("another IPv6 browser port was accepted")
        assert exc.value.response.status_code == 403
