"""Dashboard-only adapters for hosts outside the portable editor writers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from superlocalmemory.hooks.portable_kit import _pin_stdio_runtime


def configure_hermes(profile: str, *, home: Path | None = None) -> bool:
    import yaml

    path = (home or Path.home()) / ".hermes" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if config is None:
        config = {}
    if not isinstance(config, dict) or not isinstance(config.get("mcp_servers", {}), dict):
        raise ValueError("Hermes settings must contain an MCP server mapping")
    block = {
        "command": "slm", "args": ["mcp"], "env": {"SLM_MCP_PROFILE": profile},
    }
    _pin_stdio_runtime(block, "hermes")
    updated = {
        **config,
        "mcp_servers": {**config.get("mcp_servers", {}), "superlocalmemory": block},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".slm.tmp")
    temporary.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return True


def configure_claude_code(profile: str) -> bool:
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("Claude Code is not installed. Install the app, then refresh this page.")
    # Use the host's supported config command rather than editing its files.
    block = {"command": "slm", "args": ["mcp"], "env": {"SLM_MCP_PROFILE": profile}}
    _pin_stdio_runtime(block, "claude-code")
    env = {**os.environ, **block["env"]}
    args = [
        executable,
        "mcp",
        "add",
        "superlocalmemory",
        "--scope",
        "user",
        "--transport",
        "stdio",
    ]
    args += [part for key, value in block["env"].items() for part in ("--env", f"{key}={value}")]
    args += ["--", block["command"], *block["args"]]
    result = subprocess.run(args, env=env, capture_output=True, timeout=30, check=False)
    return result.returncode == 0
