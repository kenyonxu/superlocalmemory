"""`slm connect openclaw` (14 Oct 2026).

OpenClaw reads an optional JSON5 config from ``~/.openclaw/openclaw.json``
(``OPENCLAW_CONFIG_PATH`` points at another file); MCP server definitions live
under ``mcp.servers`` (docs.openclaw.ai: gateway/configuration, tools/mcp). It
loads skills from ``~/.agents/skills`` among other folders (tools/skills).

JSON5 allows comments and trailing commas, which a JSON rewrite would drop, so
a file that is not plain JSON is never rewritten: the result names the
`openclaw mcp add` command instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from superlocalmemory.hooks.portable_kit import IDE_MATRIX, connect_ide, global_config_path


def _cfg(home: Path) -> Path:
    return home / ".openclaw" / "openclaw.json"


def test_a_new_config_gets_the_server_under_mcp_servers(tmp_path: Path) -> None:
    result = connect_ide("openclaw", home=tmp_path)
    assert result["error"] is None, result
    block = json.loads(_cfg(tmp_path).read_text())["mcp"]["servers"]["superlocalmemory"]
    assert block["args"][-1] == "mcp" and "command" in block


def test_plain_json_keeps_everything_else(tmp_path: Path) -> None:
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"agents": {"defaults": {"model": "x"}},
                                "mcp": {"servers": {"docs": {"url": "https://e/mcp"}}}}))
    assert connect_ide("openclaw", home=tmp_path)["error"] is None
    data = json.loads(path.read_text())
    assert data["agents"] == {"defaults": {"model": "x"}}
    assert set(data["mcp"]["servers"]) == {"docs", "superlocalmemory"}


def test_a_json5_file_with_comments_is_never_rewritten(tmp_path: Path) -> None:
    path = _cfg(tmp_path)
    path.parent.mkdir(parents=True)
    text = '{\n  // my gateway\n  agents: { defaults: { model: "x" } },\n}\n'
    path.write_text(text)
    result = connect_ide("openclaw", home=tmp_path)
    assert result["error"] and "openclaw mcp add superlocalmemory" in result["error"]
    assert path.read_text() == text


def test_honours_openclaw_config_path() -> None:
    desc = IDE_MATRIX["openclaw"]
    home = Path("/home/someone")
    assert global_config_path(desc, home, environ={"OPENCLAW_CONFIG_PATH": "/x/oc.json"}) == Path("/x/oc.json")
    assert global_config_path(desc, home, environ={}) == _cfg(home)


def test_skills_are_installed_where_openclaw_loads_them(tmp_path: Path) -> None:
    result = connect_ide("openclaw", home=tmp_path)
    assert result["error"] is None, result
    for skill in ("slm-recall", "slm-remember", "slm-session", "slm-mesh"):
        assert (tmp_path / ".agents" / "skills" / skill / "SKILL.md").exists(), skill
    assert not (tmp_path / ".codex").exists(), "OpenClaw setup must not write Codex agents"
