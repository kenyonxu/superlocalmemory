"""A same-machine proxy preserves its configured public Host and remote auth."""
# ruff: noqa: F811 -- imported pytest fixture is requested as a parameter

import json
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import uvicorn

from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@contextmanager
def running_backend(app, listener):
    from superlocalmemory.server.forwarded_guard import uvicorn_proxy_options

    server = uvicorn.Server(uvicorn.Config(
        app, lifespan="on", access_log=False, log_level="error",
        **uvicorn_proxy_options(),
    ))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]},
                              name="test-configured-backend")
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield
    finally:
        server.should_exit = True
        worker.join(10)
        listener.close()
        assert not worker.is_alive()


@pytest.fixture
def configured_backend(engine_with_mock_deps, monkeypatch, request):
    import asyncio

    from superlocalmemory.mcp import server as mcp_module
    from superlocalmemory.server import unified_daemon

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in (8765, 8767)
    monkeypatch.setenv("SLM_DAEMON_PORT", str(port))
    monkeypatch.setenv("SLM_ALLOWED_HOSTS", "slm.office.lan")
    monkeypatch.setenv("SLM_MCP_ALLOWED_HOSTS", "slm.office.lan:*,127.0.0.1:*")
    monkeypatch.setenv("SLM_TRUSTED_PROXIES", "127.0.0.1")
    app = request.getfixturevalue("app_and_engine")
    monkeypatch.setattr(mcp_module, "_engine", engine_with_mock_deps)
    mcp_app = unified_daemon._mcp_app

    @asynccontextmanager
    async def lifespan(_):
        async with mcp_app.router.lifespan_context(mcp_app):
            app.state.test_loop = asyncio.get_running_loop()
            yield

    app.router.lifespan_context = lifespan
    with running_backend(app, listener):
        yield app, port


def test_public_host_proxy_keeps_remote_key_and_tls_checks(configured_backend, monkeypatch):
    from superlocalmemory.server.remote_keys import default_store

    app, port = configured_backend
    _, key = default_store().add("test-proxy-reader", "read", profile="default")

    class Forwarder(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = dict(self.headers)
            headers["X-Forwarded-For"] = "127.0.0.1"  # a claimed local caller stays remote
            headers["X-Forwarded-Proto"] = headers.pop("X-Test-Proto", "https")
            response = httpx.post(f"http://127.0.0.1:{port}{self.path}", content=body,
                                  headers=headers, trust_env=False)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Forwarder)
    assert proxy.server_port not in (8765, 8767)
    worker = threading.Thread(target=proxy.serve_forever, name="test-public-host-proxy")
    worker.start()
    try:
        target = f"http://127.0.0.1:{proxy.server_port}/mcp/"
        headers = {"Host": f"slm.office.lan:{proxy.server_port}",
                   "Accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}}}
        with httpx.Client(trust_env=False) as client:
            anonymous = client.post(target, json=body, headers=headers)
            assert anonymous.status_code == 401
            assert anonymous.json()["error"] == "remote_auth_required"
            authorized = client.post(target, json=body, headers={
                **headers, "Authorization": "Bearer " + key,
            })
            assert authorized.status_code == 200
            result = authorized.json()["result"]
            assert not result.get("isError")
            assert json.loads(result["content"][0]["text"])["success"] is True
            plaintext = client.post(target, json=body, headers={
                **headers, "Authorization": "Bearer " + key, "X-Test-Proto": "http",
            })
            assert plaintext.status_code == 403
            assert plaintext.json()["error"] == "remote_requires_tls"
            foreign = client.post(target, json=body, headers={
                **headers, "Authorization": "Bearer " + key, "Host": "unlisted.example",
            })
            assert foreign.status_code == 421
    finally:
        proxy.shutdown()
        proxy.server_close()
        worker.join(5)
        assert not worker.is_alive()


@pytest.mark.parametrize("variable", ["SLM_ALLOWED_HOSTS", "SLM_MCP_ALLOWED_HOSTS"])
def test_loopback_accepts_finite_configuration_only(monkeypatch, variable):
    from superlocalmemory.server.host_guard import listener_host_allowed

    monkeypatch.delenv("SLM_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SLM_MCP_ALLOWED_HOSTS", raising=False)
    scope = {"server": ("127.0.0.1", 40000),
             "headers": [(b"host", b"slm.office.lan:443")]}
    for setting in ("*", "*.office.lan", "other.office.lan:*"):
        monkeypatch.setenv(variable, setting)
        assert not listener_host_allowed(scope)
    monkeypatch.setenv(variable, "slm.office.lan:*")
    assert listener_host_allowed(scope)
