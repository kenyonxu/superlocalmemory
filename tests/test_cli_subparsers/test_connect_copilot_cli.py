"""`slm connect copilot-cli`: GitHub Copilot CLI and VS Code's Agents window (14 Oct 2026).

VS Code's Agents window runs agents in a separate Agent Host process that does
not see MCP servers provided by editor extensions; it reads
``~/.copilot/mcp-config.json`` natively, as GitHub Copilot CLI does. Per GitHub's
docs ("Adding MCP servers for GitHub Copilot CLI") the file holds
``mcpServers`` entries like ``{"type": "local", "command", "args", "env",
"tools": ["*"]}``, and ``COPILOT_HOME`` moves the folder.
"""

from __future__ import annotations

import json
from pathlib import Path

from superlocalmemory.hooks.portable_kit import IDE_MATRIX, connect_ide, global_config_path


def test_writes_a_local_server_with_every_tool(tmp_path: Path) -> None:
    result = connect_ide("copilot-cli", home=tmp_path)
    assert result["error"] is None, result
    data = json.loads((tmp_path / ".copilot" / "mcp-config.json").read_text())
    block = data["mcpServers"]["superlocalmemory"]
    assert block["type"] == "local"
    assert block["tools"] == ["*"]
    assert block["args"][-1] == "mcp"


def test_keeps_other_servers(tmp_path: Path) -> None:
    path = tmp_path / ".copilot" / "mcp-config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"playwright": {"type": "local", "command": "npx"}}}))
    assert connect_ide("copilot-cli", home=tmp_path)["error"] is None
    assert set(json.loads(path.read_text())["mcpServers"]) == {"playwright", "superlocalmemory"}


def test_honours_copilot_home() -> None:
    desc = IDE_MATRIX["copilot-cli"]
    home = Path("/home/someone")
    assert global_config_path(desc, home, environ={"COPILOT_HOME": "/c"}) == Path("/c/mcp-config.json")
    assert global_config_path(desc, home, environ={}) == home / ".copilot" / "mcp-config.json"
