"""Local setup preserves other apps and writes executable, root-bound MCP blocks."""

import sys
from types import SimpleNamespace

import yaml

from superlocalmemory.server import local_host_setup


def test_hermes_setup_preserves_other_settings_and_selects_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SLM_REQUIRE_CREDENTIALS", "1")
    config = tmp_path / "home" / ".hermes" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        yaml.safe_dump({"other_setting": 42, "mcp_servers": {"other": {"command": "other"}}})
    )
    assert local_host_setup.configure_hermes("core") is True
    updated = yaml.safe_load(config.read_text())
    assert updated["other_setting"] == 42
    assert updated["mcp_servers"]["other"] == {"command": "other"}
    block = updated["mcp_servers"]["superlocalmemory"]
    assert block["command"] == sys.executable
    assert block["env"]["SLM_MCP_PROFILE"] == "core"
    assert block["env"]["SLM_REQUIRE_CREDENTIALS"] == "1"
    assert block["env"]["SLM_DATA_DIR"] == str((tmp_path / "data").resolve())


def test_claude_setup_uses_host_command_and_absolute_python(monkeypatch):
    seen = []
    monkeypatch.setenv("SLM_REQUIRE_CREDENTIALS", "1")
    monkeypatch.setattr(local_host_setup.shutil, "which", lambda _: "/sandbox/claude")
    monkeypatch.setattr(
        local_host_setup.subprocess,
        "run",
        lambda args, **kw: seen.append(args) or SimpleNamespace(returncode=0),
    )
    assert local_host_setup.configure_claude_code("code") is True
    command = seen[0]
    assert command[:4] == ["/sandbox/claude", "mcp", "add", "superlocalmemory"]
    assert "SLM_MCP_PROFILE=code" in command
    assert "SLM_REQUIRE_CREDENTIALS=1" in command
    assert command[command.index("--") + 1] == sys.executable
