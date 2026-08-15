"""Safety contract for consented host-integration upgrades."""

from __future__ import annotations

import sys
from argparse import Namespace
from types import ModuleType, SimpleNamespace

import pytest


def test_ordinary_cli_commands_never_auto_install_claude_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package upgrade must not mutate Claude configuration on normal use."""
    import superlocalmemory.cli.commands as commands
    import superlocalmemory.hooks.claude_code_hooks as claude_hooks

    calls: list[None] = []

    def unexpected_mutation() -> None:
        calls.append(None)

    monkeypatch.setattr(claude_hooks, "auto_install_if_needed", unexpected_mutation)
    monkeypatch.setattr(commands, "cmd_status", lambda _args: None)

    commands.dispatch(Namespace(command="status", dry_run=False))

    assert calls == []


def test_mcp_startup_never_auto_installs_claude_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-Claude host must not mutate Claude during its MCP startup."""
    import superlocalmemory.cli.commands as commands
    import superlocalmemory.hooks.claude_code_hooks as claude_hooks

    calls: list[None] = []
    monkeypatch.setattr(claude_hooks, "auto_install_if_needed", lambda: calls.append(None))

    reaper = ModuleType("superlocalmemory.infra.process_reaper")
    reaper.ReaperConfig = lambda **_kwargs: object()
    reaper.find_orphans = lambda _config: []
    reaper.is_mcp_server_process = lambda _orphan: False
    reaper.kill_orphan = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, reaper.__name__, reaper)

    integrity = ModuleType("superlocalmemory.infra.version_integrity")
    integrity.check_version_integrity = lambda: SimpleNamespace(is_stale=False, differs=False)
    monkeypatch.setitem(sys.modules, integrity.__name__, integrity)

    server_module = ModuleType("superlocalmemory.mcp.server")
    server_module.server = SimpleNamespace(run=lambda **_kwargs: None)
    monkeypatch.setitem(sys.modules, server_module.__name__, server_module)

    commands.cmd_mcp(Namespace())

    assert calls == []


def test_upgrade_hosts_apply_requires_at_least_one_explicit_target() -> None:
    """An apply cannot silently discover-and-edit every known host."""
    from superlocalmemory.cli.host_upgrades import validate_upgrade_request

    with pytest.raises(ValueError, match="--host or --all-detected"):
        validate_upgrade_request(apply=True, hosts=[], all_detected=False)


def test_existing_portable_mcp_block_is_verified_not_rewritten() -> None:
    """Upgrading must retain host-specific command and environment details."""
    from superlocalmemory.cli.host_upgrades import _upgrade_host

    result = _upgrade_host("cursor", apply=True, already_integrated=True)

    assert result["status"] == "verified"
    assert "preserved" in result["detail"]


def test_existing_hermes_mapping_is_verified_not_rewritten() -> None:
    from superlocalmemory.cli.host_upgrades import _upgrade_host

    result = _upgrade_host("hermes", apply=True, already_integrated=True)

    assert result["status"] == "verified"
    assert "preserved" in result["detail"]


def test_npm_install_and_interactive_setup_explain_host_upgrade_path() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "scripts" / "postinstall.js",
        root / "scripts" / "postinstall-interactive.js",
    ):
        assert "slm upgrade-hosts" in path.read_text(encoding="utf-8"), path


def test_host_upgrade_docs_exist_in_readme_docs_and_wiki_source() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert "docs/host-upgrades.md" in (root / "README.md").read_text(encoding="utf-8")
    assert (root / "docs" / "host-upgrades.md").is_file()
    assert (root / "wiki-content" / "Host-Upgrades.md").is_file()
    assert "Host Integration Upgrades" in (
        root / "wiki-content" / "_Sidebar.md"
    ).read_text(encoding="utf-8")
