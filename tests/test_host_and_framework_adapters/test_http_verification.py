"""Actual sandbox daemon and MCP; only its embedding provider is a local fixture."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def embedding_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            inputs = request.get("input", [])
            count = len(inputs) if isinstance(inputs, list) else 1
            body = json.dumps({"data": [{"embedding": [0.25] * 8} for _ in range(count)]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def wait_for_health(port, process, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as reply:
                if json.load(reply).get("status") == "ok":
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return False


@pytest.fixture
def sandbox_daemon(tmp_path, embedding_server):
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    root = Path(os.environ["SLM_DATA_DIR"])
    config = SLMConfig.for_mode(
        Mode.A,
        base_dir=root,
        embedding_provider="openai",
        embedding_endpoint=embedding_server,
        embedding_model_name="fixture",
        embedding_dimension=8,
    )
    config.retrieval.use_cross_encoder = False
    config.save(root / "config.json")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "SLM_DAEMON_PORT": str(port), "HF_HUB_OFFLINE": "1"}
    with (tmp_path / "daemon.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "superlocalmemory.server.unified_daemon",
                "--start",
                f"--port={port}",
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            assert wait_for_health(port, process), (tmp_path / "daemon.log").read_text()[-4000:]
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.slow
def test_brn18_ux42_real_daemon_and_mcp(tmp_path, sandbox_daemon):
    from superlocalmemory.hooks.portable_kit import _check_daemon_health, connect_ide
    from superlocalmemory.hooks.verify_mcp import verify_http_mcp

    port = sandbox_daemon
    assert _check_daemon_health(port)
    receipt = verify_http_mcp(port, profile="full", timeout=5)
    assert receipt["verified"], receipt
    assert "slm_recall" in receipt["tools"] or "recall" in receipt["tools"]
    result = connect_ide("antigravity", home=tmp_path / "host", transport="http", daemon_port=port)
    assert result["error"] is None
