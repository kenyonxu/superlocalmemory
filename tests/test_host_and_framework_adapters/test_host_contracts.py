"""WP-7 host regressions; all filesystem and socket operations are isolated."""

import json
import sys
import threading
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonschema
import pytest
import yaml

from superlocalmemory.hooks import portable_kit as kit
from superlocalmemory.infra.data_root import canonical_data_root

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("transport", ["stdio", "http", "http-mcp-remote"])
def test_ux33_opencode_uses_official_schema(tmp_path, monkeypatch, transport):
    monkeypatch.setattr(kit, "_check_daemon_health", lambda *_: True)
    result = kit.connect_ide(
        "opencode", home=tmp_path, transport=transport, daemon_port=29177, profile="full"
    )
    assert result["error"] is None
    assert result["mcp_path"] == str(tmp_path / ".config/opencode/opencode.json")
    data = json.loads(Path(result["mcp_path"]).read_text())
    jsonschema.validate(data["mcp"], json.loads((FIXTURES / "opencode-mcp.json").read_text()))
    server = data["mcp"]["superlocalmemory"]
    if transport != "http":
        assert server["environment"]["SLM_MCP_PROFILE"] == "full"


@pytest.mark.parametrize("existing", [False, True])
def test_brn27_ux34_continue_retains_mcp_servers(tmp_path, existing):
    config = tmp_path / ".continue/config.yaml"
    original = {
        "name": "My Config",
        "version": "2.0.0",
        "schema": "v1",
        "models": [],
        "rules": ["Keep this"],
        "mcpServers": [{"name": "other", "command": "other"}],
    }
    if existing:
        config.parent.mkdir()
        config.write_text(yaml.safe_dump(original))
    result = kit.connect_ide("continue", home=tmp_path, profile="full")
    assert result["error"] is None
    data = yaml.safe_load(config.read_text())
    assert data["name"] and data["version"] and data["schema"] == "v1"
    assert data["mcpServers"][-1] == {
        "name": "superlocalmemory",
        "command": sys.executable,
        "args": ["-m", "superlocalmemory.cli", "mcp"],
        "env": {
            "SLM_MCP_PROFILE": "full",
            "SLM_MCP_HOST": "continue",
            "SLM_DATA_DIR": str(canonical_data_root()),
        },
    }
    if existing:
        assert data["rules"] == original["rules"]
        assert data["mcpServers"][0] == original["mcpServers"][0]
    assert kit.connect_ide("continue", home=tmp_path, profile="full")["mcp_config"] == "unchanged"


@pytest.mark.parametrize("here", [False, True])
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_ux38_antigravity_documented_path(tmp_path, monkeypatch, here, transport):
    monkeypatch.setattr(kit, "_check_daemon_health", lambda *_: True)
    result = kit.connect_ide(
        "antigravity",
        home=tmp_path,
        project=tmp_path,
        here=here,
        transport=transport,
        daemon_port=29177,
    )
    path = tmp_path / (".agents/mcp_config.json" if here else ".gemini/config/mcp_config.json")
    assert result["mcp_path"] == str(path)
    block = json.loads(path.read_text())["mcpServers"]["superlocalmemory"]
    assert block == (
        {"serverUrl": "http://127.0.0.1:29177/mcp/"}
        if transport == "http"
        else {
            "command": sys.executable,
            "args": ["-m", "superlocalmemory.cli", "mcp"],
            "env": {"SLM_MCP_HOST": "antigravity", "SLM_DATA_DIR": str(canonical_data_root())},
        }
    )
    assert result["host_verification"] == "pending"


def test_brn18_ux42_verifier_uses_real_health_route(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(b'{"status":"ok","ready":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setattr("superlocalmemory.cli.daemon.owned_daemon_answers", lambda *_: True)
    try:
        assert kit._check_daemon_health(server.server_port)
        assert seen == ["/health"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_brn20_discovery_matches_installation(tmp_path, monkeypatch, capsys):
    from superlocalmemory.cli.commands import cmd_connect

    monkeypatch.setenv("HOME", str(tmp_path))
    cmd_connect(Namespace(ide=None, list=True, json=True))
    data = json.loads(capsys.readouterr().out)["data"]
    hosts = data["ides"]
    assert {h["ide_id"] for h in hosts} == set(kit.supported_ides())
    assert all(h["install_method"] in {"mcp-config", "native-plugin"} for h in hosts)
    assert (
        next(h for h in hosts if h["ide_id"] == "claude-code")["install_method"] == "native-plugin"
    )


def test_brn20_codex_counts_come_from_installer(monkeypatch, capsys):
    from superlocalmemory.cli.commands import cmd_codex
    from superlocalmemory.hooks import codex_assets

    monkeypatch.setattr(codex_assets, "install_assets", lambda **kw: {"success": True})
    monkeypatch.setattr(
        "superlocalmemory.hooks.codex_hooks.install_hooks", lambda **kw: {"success": True}
    )
    cmd_codex(Namespace(action="install", dry_run=True, json=False))
    output = capsys.readouterr().out
    assert f"{len(codex_assets.SKILLS)} skills" in output
    assert f"{len(codex_assets.AGENTS)} subagents" in output


@pytest.mark.slow
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("transport", ["stdio", "http", "http-mcp-remote"])
def test_continue_actual_parser(tmp_path, monkeypatch, existing, transport):
    import os
    import subprocess

    parser_root = os.environ.get("WP7_CONTINUE_NODE_MODULES")
    assert parser_root, "set WP7_CONTINUE_NODE_MODULES to @continuedev/config-yaml installation"
    config = tmp_path / ".continue/config.yaml"
    if existing:
        config.parent.mkdir()
        config.write_text(
            "name: User\nversion: 2.0.0\nschema: v1\nmodels: []\n"
            "rules: [Preserve]\nmcpServers: [{name: other, command: other}]\n"
        )
    monkeypatch.setattr(kit, "_check_daemon_health", lambda *_: True)
    result = kit.connect_ide("continue", home=tmp_path, transport=transport, daemon_port=29177)
    assert result["error"] is None
    code = """
import {configYamlSchema} from '@continuedev/config-yaml';
import YAML from 'yaml';
import fs from 'node:fs';
const parsed = configYamlSchema.parse(YAML.parse(fs.readFileSync(process.argv[1], 'utf8')));
if (!parsed.mcpServers.some(s => s.name === 'superlocalmemory' &&
    (s.command === process.argv[2] || s.command === 'mcp-remote' || s.type === 'streamable-http')))
  throw Error('Parser discarded SLM MCP server');
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", code, str(config), sys.executable],
        cwd=parser_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_verify_reports_protocol_result(tmp_path, monkeypatch, capsys):
    from superlocalmemory.cli.commands import cmd_connect

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(kit, "_check_daemon_health", lambda *_: True)
    monkeypatch.setattr(
        "superlocalmemory.hooks.verify_mcp.verify_http_mcp",
        lambda *a, **kw: {"verified": True, "tools": ["recall"], "error": None},
    )
    cmd_connect(
        Namespace(ide="opencode", transport="http", daemon_port=29177, verify=True, json=False)
    )
    assert "MCP verified" in capsys.readouterr().out


def test_gemini_http_uses_documented_transport_key(tmp_path, monkeypatch):
    monkeypatch.setattr(kit, "_check_daemon_health", lambda *_: True)
    result = kit.connect_ide("gemini-cli", home=tmp_path, transport="http", daemon_port=29177)
    data = json.loads(Path(result["mcp_path"]).read_text())
    assert data["mcpServers"]["superlocalmemory"] == {"httpUrl": "http://127.0.0.1:29177/mcp/"}
