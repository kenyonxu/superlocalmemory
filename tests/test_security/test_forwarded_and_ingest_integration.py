"""Real proxy demotion and deterministic walk/open substitution probes."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import hashlib
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@contextmanager
def _serve(app, listener):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        app, lifespan="off", proxy_headers=False, access_log=False,
        log_level="error", ws="none",
    ))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]},
                              name="test-http-daemon")
    worker.start()
    try:
        until = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < until:
            time.sleep(0.01)
        assert server.started
        yield
    finally:
        server.should_exit = True
        worker.join(5)
        listener.close()
        assert not worker.is_alive()


def test_real_reverse_proxy_never_borrows_loopback_trust(
        engine_with_mock_deps, tmp_path, monkeypatch, request):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    assert port not in (8765, 8767)
    monkeypatch.setenv("SLM_DAEMON_PORT", str(port))
    app = request.getfixturevalue("app_and_engine")
    backend = f"http://127.0.0.1:{port}"
    (tmp_path / "api_key").write_text("test-proxy-api-credential", encoding="utf-8")

    class Forwarder(BaseHTTPRequestHandler):
        def do_GET(self):
            headers = dict(self.headers)
            headers["X-Forwarded-For"] = headers.get("X-Forwarded-For", "203.0.113.10")
            response = httpx.get(backend + self.path, headers=headers, trust_env=False)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    with _serve(app, listener):
        assert httpx.get(backend + "/api/memories", trust_env=False).status_code == 200
        for host in ("192.0.2.10", "attacker.example"):
            assert httpx.get(backend + "/internal/token", headers={"Host": host},
                             trust_env=False).status_code == 421
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Forwarder)
        proxy_thread = threading.Thread(target=proxy.serve_forever, name="test-reverse-proxy")
        proxy_thread.start()
        try:
            url = f"http://127.0.0.1:{proxy.server_port}"
            for claimed in ("127.0.0.1", "203.0.113.10", "127.0.0.1, 203.0.113.10"):
                for path in ("/api/memories", "/internal/token"):
                    response = httpx.get(url + path, headers={"X-Forwarded-For": claimed},
                                         trust_env=False)
                    assert response.status_code == 401
                    assert response.json()["error"] == "remote_auth_required"
            authorized = httpx.get(url + "/api/memories", headers={
                "X-SLM-API-Key": "test-proxy-api-credential",
            }, trust_env=False)
            assert authorized.status_code == 200
        finally:
            proxy.shutdown()
            proxy.server_close()
            proxy_thread.join(5)
            assert not proxy_thread.is_alive()


@pytest.mark.parametrize("swap", ["symlink", "regular_replacement"])
def test_folder_walk_to_open_substitution_is_refused(tmp_path, swap):
    from superlocalmemory.sources.ignore import IgnoreRules
    from superlocalmemory.sources.ingest import load_verified
    from superlocalmemory.sources.walk import walk_tree

    root = tmp_path / "folder"
    root.mkdir()
    original = b"folder memory marker"
    path = root / "note.txt"
    path.write_bytes(original)
    entry, = walk_tree(root, IgnoreRules(root)).entries
    digest = hashlib.sha256(original).hexdigest()
    assert load_verified(path, entry.file_id, digest, "text") == original
    outside = tmp_path / "outside.txt"
    # Identical bytes make the inode check independently observable even when
    # the hash would match. Retain the old inode to prevent filesystem reuse.
    outside.write_bytes(original)
    path.rename(root / "retained.txt")
    if swap == "symlink":
        path.symlink_to(outside)
    else:
        path.write_bytes(original)
    with pytest.raises(OSError):
        load_verified(path, entry.file_id, digest, "text")
