"""`slm connect muse` and `slm connect grok-cli` (14 Oct 2026).

Muse Code keeps MCP servers in ``$XDG_CONFIG_HOME/muse/settings.json`` (else
``~/.config/muse/settings.json``) under ``mcpServers``. A new file starts as
``{"schema_version": 1, ...}``, and a file that still carries the legacy
``mcp_servers`` key must have it renamed in the same edit: when both keys are
present Muse drops every MCP server. (Muse Code 1.4.7, its built-in MCP setup
guide.)

Grok CLI keeps them in ``~/.grok/config.toml`` as ``[mcp_servers.<name>]``
tables with ``command``, ``args`` and ``env``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from superlocalmemory.hooks.portable_kit import IDE_MATRIX, connect_ide, global_config_path


def _muse(home: Path) -> Path:
    return home / ".config" / "muse" / "settings.json"


def test_muse_new_settings_file_gets_schema_version_and_the_server(tmp_path: Path) -> None:
    result = connect_ide("muse", home=tmp_path)
    assert result["error"] is None, result
    data = json.loads(_muse(tmp_path).read_text())
    assert data["schema_version"] == 1
    block = data["mcpServers"]["superlocalmemory"]
    assert block["type"] == "stdio"
    assert block["args"][-1] == "mcp"


def test_muse_keeps_other_settings_and_servers(tmp_path: Path) -> None:
    path = _muse(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1, "model": "spark",
        "mcpServers": {"docs": {"type": "streamable-http", "url": "https://example/mcp"}},
    }))
    assert connect_ide("muse", home=tmp_path)["error"] is None
    data = json.loads(path.read_text())
    assert data["model"] == "spark"
    assert set(data["mcpServers"]) == {"docs", "superlocalmemory"}


def test_muse_legacy_mcp_servers_key_is_renamed_not_duplicated(tmp_path: Path) -> None:
    path = _muse(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1,
                                "mcp_servers": {"docs": {"type": "stdio", "command": "d"}}}))
    assert connect_ide("muse", home=tmp_path)["error"] is None
    data = json.loads(path.read_text())
    assert "mcp_servers" not in data, "both keys present makes Muse drop every server"
    assert set(data["mcpServers"]) == {"docs", "superlocalmemory"}


def test_muse_honours_xdg_config_home() -> None:
    home = Path("/home/someone")
    path = global_config_path(IDE_MATRIX["muse"], home, environ={"XDG_CONFIG_HOME": "/x/cfg"})
    assert path == Path("/x/cfg/muse/settings.json")
    assert global_config_path(IDE_MATRIX["muse"], home, environ={}) == _muse(home)


def test_grok_cli_writes_a_mcp_servers_table_and_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / ".grok" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('model = "grok-4.7"\n\n[mcp_servers.hub]\ncommand = "mcp-remote"\n')
    assert connect_ide("grok-cli", home=tmp_path)["error"] is None
    data = tomllib.loads(path.read_text())
    assert data["model"] == "grok-4.7"
    assert set(data["mcp_servers"]) == {"hub", "superlocalmemory"}
    assert data["mcp_servers"]["superlocalmemory"]["args"][-1] == "mcp"


def test_muse_server_is_optional_so_a_failed_start_never_aborts_the_run(tmp_path: Path) -> None:
    """Muse's default mode is `required`: "If a required server fails to start,
    the whole run aborts" (dev.meta.ai/docs/muse-code/configuration)."""
    connect_ide("muse", home=tmp_path)
    block = json.loads(_muse(tmp_path).read_text())["mcpServers"]["superlocalmemory"]
    assert block["mode"] == "optional"


def test_grok_cli_honours_grok_home() -> None:
    """docs.x.ai/build/settings: set `$GROK_HOME` to move the home directory."""
    home = Path("/home/someone")
    desc = IDE_MATRIX["grok-cli"]
    assert global_config_path(desc, home, environ={"GROK_HOME": "/g"}) == Path("/g/config.toml")
    assert global_config_path(desc, home, environ={}) == home / ".grok" / "config.toml"
