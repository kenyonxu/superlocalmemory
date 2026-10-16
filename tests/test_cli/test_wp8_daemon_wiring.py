"""Installation identity and host wiring stay bound to their namespace."""

import plistlib
import sys
from argparse import ArgumentParser
from types import SimpleNamespace

import pytest


def test_service_labels_isolate_roots_and_ports(monkeypatch, tmp_path):
    from superlocalmemory.cli import service_installer as service

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "first"))
    monkeypatch.setenv("SLM_DAEMON_PORT", "19281")
    first = service._macos_plist_path()
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "second"))
    second = service._macos_plist_path()
    assert first != second
    monkeypatch.setenv("SLM_DAEMON_PORT", "19282")
    assert second != service._macos_plist_path()


def test_services_preserve_strict_policy_without_secrets(monkeypatch):
    from superlocalmemory.cli import service_installer as service

    monkeypatch.setenv("SLM_REQUIRE_CREDENTIALS", "1")
    monkeypatch.setenv("SLM_REQUIRE_API_KEY_LOOPBACK", "1")
    monkeypatch.setenv("SLM_DISABLE_LEGACY_PORT", "1")
    monkeypatch.setenv("SLM_API_KEY", "fixture-secret")
    payload = plistlib.loads(service._macos_plist_content().encode())
    assert payload["EnvironmentVariables"]["SLM_REQUIRE_CREDENTIALS"] == "1"
    assert payload["EnvironmentVariables"]["SLM_DISABLE_LEGACY_PORT"] == "1"
    for content in (service._linux_service_content(), service._windows_vbs_content()):
        assert "SLM_REQUIRE_CREDENTIALS" in content
        assert "SLM_REQUIRE_API_KEY_LOOPBACK" in content
        assert "fixture-secret" not in content
    assert "fixture-secret" not in str(payload)


def test_stdio_connect_uses_absolute_interpreter():
    from superlocalmemory.hooks.portable_kit import IDE_MATRIX, _build_server_block

    block = _build_server_block(IDE_MATRIX["cursor"], "stdio", 19281, None)
    assert block["command"] == sys.executable
    assert block["args"] == ["-m", "superlocalmemory.cli", "mcp"]


def test_daemon_config_reports_runtime_port(monkeypatch):
    from superlocalmemory.server.routes import config_api

    monkeypatch.setattr(config_api, "_read_config", lambda: {"daemon_port": 19281})
    monkeypatch.setenv("SLM_DAEMON_PORT", "19282")
    assert config_api.get_daemon_config()["port"] == 19282


@pytest.mark.parametrize("command,field", [("dashboard", "port"), ("connect", "daemon_port")])
def test_parser_leaves_port_absence_distinguishable(monkeypatch, command, field):
    import importlib

    cli = importlib.import_module("superlocalmemory.cli.main")
    original_parse = ArgumentParser.parse_args
    captured = []

    def capture(parser, *args, **kwargs):
        captured.append(original_parse(parser, [command]))
        raise RuntimeError("parser captured")

    monkeypatch.setattr(sys, "argv", ["slm", "--help"])
    monkeypatch.setattr(ArgumentParser, "parse_args", capture)
    with pytest.raises(RuntimeError, match="parser captured"):
        cli.main()
    assert getattr(captured[0], field) is None


def test_dashboard_uses_saved_port_without_probing_defaults(monkeypatch, tmp_path):
    import webbrowser

    from superlocalmemory.cli import commands, daemon

    monkeypatch.delenv("SLM_DAEMON_PORT", raising=False)
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text('{"daemon_port":19283}')
    started = []
    opened = []
    monkeypatch.setattr(daemon, "ensure_daemon", lambda **kw: started.append(kw) or True)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    commands.cmd_dashboard(SimpleNamespace(port=None))
    assert started == [{"port": 19283}]
    assert opened == ["http://localhost:19283"]


def test_second_service_install_never_unloads_first(monkeypatch, tmp_path):
    from superlocalmemory.cli import service_installer as service

    calls = []
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0, stderr=""),
    )
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "one"))
    first = service._macos_plist_path()
    assert service.install_macos()
    calls.clear()
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path / "two"))
    assert service.install_macos()
    assert all(str(first) not in call for call in calls)
    assert first.exists()


def test_release_connector_keeps_the_selected_daemon_port(monkeypatch, tmp_path):
    from superlocalmemory.cli import commands
    from superlocalmemory.hooks import portable_kit

    monkeypatch.delenv("SLM_DAEMON_PORT", raising=False)
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text('{"daemon_port":19283}')
    seen = []
    monkeypatch.setattr(
        portable_kit,
        "connect_ide",
        lambda host, **kw: (
            seen.append(kw)
            or {"ide": host, "error": None, "mcp_config": "would_write", "mcp_path": "sandbox"}
        ),
    )
    commands.cmd_connect(
        SimpleNamespace(ide="codex", daemon_port=None, transport="http", dry_run=True, json=True)
    )
    assert seen[0]["daemon_port"] == 19283


def test_opencode_command_list_is_pinned_without_changing_its_schema():
    from superlocalmemory.hooks.portable_kit import IDE_MATRIX, _build_server_block

    block = _build_server_block(IDE_MATRIX["opencode"], "stdio", 19281, "core")
    assert block["command"] == [sys.executable, "-m", "superlocalmemory.cli", "mcp"]
    assert block["environment"]["SLM_MCP_PROFILE"] == "core"
    assert block["environment"]["SLM_MCP_HOST"] == "opencode"


def test_settings_reports_bound_port_when_it_differs_from_environment(monkeypatch):
    from superlocalmemory.server.routes import config_api

    monkeypatch.setenv("SLM_DAEMON_PORT", "19282")
    monkeypatch.setattr(config_api, "_read_config", lambda: {"daemon_port": 19281})
    request = SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(daemon_descriptor=SimpleNamespace(port=19284))))
    assert config_api.get_daemon_config(request)["port"] == 19284


def test_gui_mcp_autostart_preserves_strict_policy(monkeypatch):
    from superlocalmemory.hooks.portable_kit import IDE_MATRIX, _build_server_block

    monkeypatch.setenv("SLM_REQUIRE_CREDENTIALS", "1")
    block = _build_server_block(IDE_MATRIX["cursor"], "stdio", 19281, "code")
    assert block["env"]["SLM_REQUIRE_CREDENTIALS"] == "1"
