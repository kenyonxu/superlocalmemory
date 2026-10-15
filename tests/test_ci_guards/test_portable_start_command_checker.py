"""The contract runner accepts pinned argv and rejects shell/PATH launches."""

from pathlib import Path

import pytest
from scripts import integration_compatibility as compat

from superlocalmemory.hooks.portable_kit import IDE_MATRIX


@pytest.mark.parametrize("command", [
    "/opt/SLM env/bin/python", "/opt/slm/bin/python3.12",
    r"C:\SLM env\Scripts\python.exe", "/opt/slm/bin/slm", r"C:\SLM\Scripts\slm.exe",
])
@pytest.mark.parametrize("array", [False, True])
def test_pinned_slm_start_commands_are_safe(command, array):
    args = ["mcp"] if Path(command.replace("\\", "/")).name in {"slm", "slm.exe"} else [
        "-m", "superlocalmemory.cli", "mcp"
    ]
    block = {"command": [command, *args]} if array else {"command": command, "args": args}
    compat._validate_start_command(block)


@pytest.mark.parametrize("block", [
    {"command": "slm", "args": ["mcp"]},
    {"command": ["slm", "mcp"]},
    {"command": "python", "args": ["-m", "superlocalmemory.cli", "mcp"]},
    {"command": "./bin/slm", "args": ["mcp"]},
    {"command": r"C:Scripts\slm.exe", "args": ["mcp"]},
    {"command": "/opt/slm/../bin/slm", "args": ["mcp"]},
    {"command": "/bin/sh", "args": ["-c", "slm mcp"]},
    {"command": "/opt/bin/python -m superlocalmemory.cli mcp"},
    {"command": "/opt/bin/slm;touch /tmp/injected", "args": ["mcp"]},
    {"command": "/opt/$(id)/slm", "args": ["mcp"]},
    {"command": "/opt/bin/slm\n", "args": ["mcp"]},
    {"command": "/opt/bin/slm", "args": "mcp"},
    {"command": "/opt/bin/slm", "args": ["mcp", "--unsafe"]},
    {"command": "/opt/bin/python", "args": ["-c", "import os"]},
    {"command": "/opt/bin/python", "args": ["-m", "other.module", "mcp"]},
    {"command": ["/opt/bin/slm", "mcp"], "args": ["--unsafe"]},
    {"command": []}, {"command": None}, {"command": [1, "mcp"]},
])
def test_shell_relative_and_injected_commands_are_rejected(block):
    with pytest.raises(AssertionError, match="unsafe start command"):
        compat._validate_start_command(block)


def test_continue_checker_reads_current_named_server_list():
    server = {"name": "superlocalmemory", "command": "/opt/bin/slm", "args": ["mcp"]}
    assert compat._extract_slm_block({"mcpServers": [server]}, IDE_MATRIX["continue"]) == server


@pytest.mark.parametrize("servers", [[], [
    {"name": "superlocalmemory"}, {"name": "superlocalmemory"}
]])
def test_continue_checker_requires_exactly_one_owned_entry(servers):
    with pytest.raises(ValueError, match="exactly one"):
        compat._extract_slm_block({"mcpServers": servers}, IDE_MATRIX["continue"])


def test_dashboard_hermes_generator_uses_the_same_safe_command(tmp_path, monkeypatch):
    import yaml

    from superlocalmemory.server.local_host_setup import configure_hermes

    monkeypatch.setenv("HOME", str(tmp_path))
    assert configure_hermes("core")
    path = tmp_path / ".hermes/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    _block = data["mcp_servers"]["superlocalmemory"]
    compat._validate_start_command(_block)
    before = path.read_bytes()
    assert configure_hermes("core")
    assert path.read_bytes() == before


def test_dashboard_claude_generator_passes_safe_argv_to_the_host(monkeypatch):
    from types import SimpleNamespace

    from superlocalmemory.server import local_host_setup

    calls = []
    monkeypatch.setattr(local_host_setup.shutil, "which", lambda _: "/sandbox/claude")
    monkeypatch.setattr(local_host_setup.subprocess, "run",
                        lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0))
    assert local_host_setup.configure_claude_code("core")
    argv = calls[0][calls[0].index("--") + 1:]
    compat._validate_start_command({"command": argv})
