"""A first upgrade can still show and reconnect pre-registry MCP processes."""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.infra import mcp_legacy, mcp_processes
from tests.test_mcp.test_stdio_handshake import REPO_ROOT, _handshake_env, _HandshakeClient


class Process:
    def __init__(self, root):
        self.pid = 301
        self.info = {
            "pid": self.pid,
            "ppid": 300,
            "cmdline": ["/sandbox/python", "-m", "superlocalmemory.cli.main", "mcp"],
        }
        self.root = root
        self.terminated = False

    def environ(self):
        return {
            "SLM_DATA_DIR": str(self.root),
            "SLM_MCP_HOST": "hermes",
            "PRIVATE_TOKEN": "fixture-only",
        }

    def uids(self):
        return SimpleNamespace(real=os.getuid())

    def parent(self):
        return SimpleNamespace(is_running=lambda: True)

    def create_time(self):
        return time.time() - 120

    def terminate(self):
        self.terminated = True


@pytest.fixture
def process(monkeypatch, tmp_path):
    item = Process(tmp_path)
    monkeypatch.setattr(mcp_legacy, "_processes", lambda: [item])
    monkeypatch.setattr(mcp_legacy, "_get_process", lambda pid: item)
    monkeypatch.setattr(mcp_processes, "process_identity", lambda pid: "legacy-start")
    return item


def test_first_upgrade_lists_unreported_live_version_without_secrets(process, tmp_path):
    view = mcp_processes.snapshot(tmp_path, installed_version="new")
    assert view["count"] == 1
    assert view["unreported_count"] == 1
    row = view["apps"][0]
    assert row["name"] == "Hermes"
    assert row["version_reported"] is False
    assert row["stale_version"] is False
    assert row["code_version"] == "unreported"
    assert "fixture-only" not in str(view)
    assert mcp_processes.request_reconnect(tmp_path, row["session_id"])
    assert process.terminated


def test_second_root_cannot_list_or_reconnect_the_first_install(process, tmp_path):
    first = mcp_processes.snapshot(tmp_path)["apps"][0]
    second = tmp_path / "second"
    assert mcp_processes.snapshot(second)["count"] == 0
    assert mcp_processes.request_reconnect(second, first["session_id"]) is False
    assert not process.terminated


def test_legacy_reconnect_refuses_reused_pid(process, tmp_path, monkeypatch):
    row = mcp_processes.snapshot(tmp_path)["apps"][0]
    monkeypatch.setattr(mcp_processes, "process_identity", lambda pid: "replacement-start")
    assert mcp_processes.request_reconnect(tmp_path, row["session_id"]) is False
    assert not process.terminated


def test_a_registered_process_is_counted_once(process, tmp_path):
    mcp_processes.register(
        tmp_path, "reported", pid=process.pid, host="hermes", version="new", tools=[]
    )
    view = mcp_processes.snapshot(tmp_path, installed_version="new")
    assert view["count"] == 1
    assert view["unreported_count"] == 0


def test_home_only_legacy_process_cannot_target_a_relocated_install(process, tmp_path):
    home = tmp_path / "home"
    default = home / ".superlocalmemory"
    relocated = tmp_path / "relocated"
    default.mkdir(parents=True)
    (default / "config.json").write_text('{"base_dir": "' + str(relocated) + '"}')
    process.environ = lambda: {"HOME": str(home), "SLM_MCP_HOST": "hermes"}
    assert mcp_processes.snapshot(default)["count"] == 0
    assert mcp_processes.snapshot(relocated)["count"] == 0
    assert not process.terminated


def test_dotdot_across_symlink_cannot_approve_the_wrong_root(process, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    (second / "deep").mkdir(parents=True)
    (first / "link").symlink_to(second / "deep", target_is_directory=True)
    selected = first / "link" / ".." / "data"
    assert selected.resolve() == second / "data"
    process.environ = lambda: {"SLM_DATA_DIR": str(selected), "SLM_MCP_HOST": "hermes"}
    assert mcp_processes.snapshot(first / "data")["count"] == 0
    assert mcp_processes.snapshot(second / "data")["count"] == 0
    assert not process.terminated


def test_macos_system_aliases_are_not_assumed_on_linux(process, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(mcp_legacy, "sys", SimpleNamespace(platform="linux"))
    assert not mcp_legacy._root_matches(
        process, {"SLM_DATA_DIR": "/private/tmp/data"}, Path("/tmp/data")
    )


@pytest.mark.parametrize(
    "command",
    [
        ["python", "-m", "superlocalmemory.server.unified_daemon", "mcp"],
        ["python", "-c", "unrelated application"],
    ],
)
def test_unrelated_processes_and_daemons_never_become_reconnect_targets(process, tmp_path, command):
    process.info["cmdline"] = command
    assert mcp_processes.snapshot(tmp_path)["count"] == 0
    assert not process.terminated


def test_real_pre_registry_stdio_is_discovered_and_reconnect_is_root_bound(tmp_path, monkeypatch):
    root, home, patch_dir = tmp_path / "data", tmp_path / "home", tmp_path / "legacy"
    for directory in (root, home, patch_dir):
        directory.mkdir()
    # A real SDK transport without the newly added presence registration.
    (patch_dir / "sitecustomize.py").write_text(
        "from mcp.server.mcpserver import MCPServer\n"
        "from superlocalmemory.mcp.http_transport import SLMFastMCP\n"
        "SLMFastMCP.run_stdio_async = MCPServer.run_stdio_async\n"
    )
    environment = _handshake_env(root, home)
    environment["PYTHONPATH"] = str(patch_dir) + os.pathsep + environment["PYTHONPATH"]
    environment.update(SLM_SKIP_DEP_CHECK="1", SLM_MCP_HOST="codex")
    clock = time.time
    monkeypatch.setattr(mcp_legacy, "time", SimpleNamespace(time=lambda: clock() + 20))
    with (tmp_path / "child.log").open("wb") as stderr:
        child = subprocess.Popen(
            [sys.executable, "-m", "superlocalmemory.cli.main", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=environment,
            cwd=REPO_ROOT,
        )
    client = _HandshakeClient(child)
    try:
        assert "result" in client.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "codex-mcp-client", "version": "1"},
            },
            timeout=20,
        )
        client.notify("notifications/initialized")
        assert "result" in client.call("tools/list", {}, timeout=10)
        row = next(r for r in mcp_processes.snapshot(root)["apps"] if r["pid"] == child.pid)
        assert row["version_reported"] is False
        assert mcp_processes.request_reconnect(root / "another-install", row["session_id"]) is False
        assert child.poll() is None
        assert mcp_processes.request_reconnect(root, row["session_id"])
        child.wait(timeout=8)
        assert not any(r["pid"] == child.pid for r in mcp_processes.snapshot(root)["apps"])
    finally:
        client.close()
        client._reader.join(timeout=1)
