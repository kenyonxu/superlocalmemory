"""A real stdio handshake creates presence; reconnect closes only that child."""

import subprocess
import sys
import time

from superlocalmemory.infra import mcp_processes
from tests.test_mcp.test_stdio_handshake import REPO_ROOT, _handshake_env, _HandshakeClient


def test_real_connected_process_is_registered_and_reconnects(tmp_path):
    root, home = tmp_path / "data", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    with (tmp_path / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "superlocalmemory.cli.main", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            env=_handshake_env(root, home), cwd=REPO_ROOT,
        )
    client = _HandshakeClient(process)
    try:
        assert mcp_processes.snapshot(root)["count"] == 0
        result = client.call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "codex-mcp-client", "version": "1"},
        }, timeout=20)
        assert "result" in result
        client.notify("notifications/initialized")
        assert "result" in client.call("tools/list", {}, timeout=10)
        deadline = time.monotonic() + 5
        while mcp_processes.snapshot(root)["count"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        view = mcp_processes.snapshot(root)
        assert view["count"] == 1
        assert view["apps"][0]["host"] == "codex"
        assert view["apps"][0]["pid"] == process.pid
        assert mcp_processes.request_reconnect(root, view["apps"][0]["session_id"])
        process.wait(timeout=8)
        assert mcp_processes.snapshot(root)["count"] == 0
        assert process.returncode == 0
    finally:
        client.close()
