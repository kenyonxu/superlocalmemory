"""run_maintenance / consolidate_cognitive act for the caller's own connection."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest


@pytest.fixture()
def fake_daemon(monkeypatch):
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
            body = json.dumps({"ok": True, "updated": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    descriptor = SimpleNamespace(port=server.server_port, capability="cap-test",
                                 instance_id="inst-test")
    from superlocalmemory.cli import daemon

    monkeypatch.setattr(daemon, "read_descriptor", lambda: descriptor)
    monkeypatch.setattr(daemon, "is_daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "descriptor_matches_health", lambda *a: True)
    monkeypatch.setattr(daemon._startup, "probe_health", lambda *a, **k: {"ok": True})
    try:
        yield seen
    finally:
        server.shutdown()


@pytest.mark.parametrize("path", ["/maintenance/run", "/consolidate/cognitive"])
def test_maintenance_calls_carry_the_callers_binding_and_session(fake_daemon, monkeypatch, path):
    from superlocalmemory.core import caller_session
    from superlocalmemory.mcp.tools_v33 import _try_daemon_post
    from superlocalmemory.server import caller_workspace

    monkeypatch.setattr(caller_workspace, "current_owner_workspace_binding",
                        lambda: "a" * 32)
    monkeypatch.setattr(caller_session, "presented_session", lambda: "session-token-1")

    async def run():
        return await asyncio.to_thread(_try_daemon_post, path, {"profile_id": "sales"})

    assert asyncio.run(run()) == {"ok": True, "updated": 0}
    (request,) = fake_daemon
    assert request["path"] == path
    assert request["x-slm-workspace-binding"] == "a" * 32
    assert request["x-slm-user-session"] == "session-token-1"
    assert request["x-slm-daemon-capability"] == "cap-test"


def test_a_refused_maintenance_call_is_not_retried_locally(monkeypatch):
    from superlocalmemory.cli import daemon
    from superlocalmemory.mcp.tools_v33 import _try_daemon_post

    monkeypatch.setattr(daemon, "is_daemon_running", lambda: True)

    def refuse(*a, **k):
        raise daemon.DaemonRefused(403, "/maintenance/run")

    monkeypatch.setattr(daemon, "daemon_request", refuse)
    with pytest.raises(daemon.DaemonRefused):
        _try_daemon_post("/maintenance/run", {"profile_id": "sales"})
