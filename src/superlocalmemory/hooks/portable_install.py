"""Small, explicit stages of the portable host installer."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any


def resolve_target(kit, ide_id, home, project, here, result):
    desc = kit.resolve_descriptor(ide_id)
    if desc is None:
        raise ValueError(f"Unknown IDE '{ide_id}'. Supported: {', '.join(kit.supported_ides())}")
    result["ide"] = desc.ide_id
    if desc.ide_id == "vscode-copilot" and not here:
        raise ValueError(
            "VS Code / Copilot integration is project-scoped. "
            "Run `slm connect vscode-copilot --here` from the project root."
        )
    if not desc.fmt:
        result["mcp_config"] = "skipped"
        result["agents_md"] = "skipped(native-plugin)"
        result["instructions"] = (
            kit.CLAUDE_CODE_PLUGIN_POINTER if ide_id == "claude-code" else desc.caveats
        )
        return desc, None, None
    if here:
        if project is None:
            raise ValueError("--here requires --project (project root path)")
        if desc.mcp_path_project is None:
            raise ValueError(f"{desc.display} has no supported project-scoped MCP config")
        scope, path = project, project / desc.mcp_path_project
    else:
        scope = home or Path.home()
        path = kit.global_config_path(desc, scope, environ=None if home is None else {})
    result["mcp_path"] = str(path)
    return desc, scope, path


def merge_config(kit, data, desc, transport, daemon_port, profile, result):
    if desc.fmt == "yaml":
        block = kit._build_server_block(desc, transport, daemon_port, profile)
        result["mcp_config"], result["servers_preserved"] = kit._merge_yaml_list(
            data,
            desc,
            None,
            block=block,
        )
        return
    if desc.ide_id == "muse":
        _prepare_muse_settings(data)
    servers = data
    for key in desc.server_key.split("."):  # OpenClaw nests them: mcp.servers
        if not isinstance(servers, dict):
            raise ValueError(f"{desc.server_key} must be an object")
        servers = servers.setdefault(key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{desc.server_key} must be an object")
    before = copy.deepcopy(servers.get("superlocalmemory"))
    servers["superlocalmemory"] = kit._build_server_block(desc, transport, daemon_port, profile)
    result["servers_preserved"] = len(servers) - 1
    result["mcp_config"] = (
        "unchanged"
        if before == servers["superlocalmemory"]
        else "wrote"
        if before is None
        else "merged"
    )


def _prepare_muse_settings(data):
    """Muse drops every MCP server when mcp_servers and mcpServers coexist."""
    data.setdefault("schema_version", 1)
    legacy = data.pop("mcp_servers", None)
    if isinstance(legacy, dict):
        current = data.setdefault("mcpServers", {})
        if isinstance(current, dict):
            for name, server in legacy.items():
                current.setdefault(name, server)


def persist_config(kit, data, desc, scope, path, source, here, dry_run, result):
    if dry_run:
        result["mcp_config"] = "would_write"
        if desc.agents_md_path is None:
            result["agents_md"] = "skipped(unsupported)"
        elif source is None:
            result["agents_md"] = "skipped(no-source)"
        else:
            try:
                content = source()
            except Exception as exc:
                result["agents_md"] = "error(source-unavailable)"
                raise ValueError(f"AGENTS.md asset lookup failed: {exc}") from exc
            if not isinstance(content, str) or not content.strip():
                raise ValueError("AGENTS.md asset is empty")
            result["agents_md"] = "would_write"
        return
    kit._atomic_write(path, data, desc.fmt)
    result["agents_md"] = kit._handle_agents_md(desc, scope, source, here)


def _load_or_explain(kit, path, desc, transport, port, profile):
    """Load the host config; for OpenClaw's JSON5, say how to add SLM by hand."""
    try:
        return kit._load_config(path, desc.fmt)
    except kit._ParseError:
        if desc.ide_id != "openclaw":
            raise
        import shutil

        slm = shutil.which("slm") or "slm"
        raise ValueError(
            f"{path} is JSON5 (comments or unquoted keys), which SLM does not rewrite. "
            f"Add SuperLocalMemory with: openclaw mcp add superlocalmemory "
            f"--command {slm} --arg mcp") from None


def install(kit, ide_id, *, home, project, here, profile, source, dry_run, transport, port):
    result: dict[str, Any] = {
        "ide": ide_id,
        "transport": transport,
        "mcp_config": "error",
        "mcp_path": "",
        "agents_md": "skipped(not-run)",
        "servers_preserved": 0,
        "error": None,
        "host_verification": "pending",
    }
    try:
        if transport not in kit.VALID_TRANSPORTS:
            raise ValueError(f"Invalid transport '{transport}'")
        if profile:
            from superlocalmemory.mcp.profile_names import canonical_profile

            canonical_profile(profile)
        desc, scope, path = resolve_target(kit, ide_id, home, project, here, result)
        if path is None:
            return result
        data = _load_or_explain(kit, path, desc, transport, port, profile)
        merge_config(kit, data, desc, transport, port, profile, result)
        if transport != "stdio" and not dry_run and not kit._check_daemon_health(port):
            print(
                f"[SLM] Warning: owned daemon not ready at http://127.0.0.1:{port}/health. "
                "Run `slm serve start` or check the configured port.",
                file=sys.stderr,
            )
        persist_config(kit, data, desc, scope, path, source, here, dry_run, result)
        if desc.ide_id == "openclaw":
            from superlocalmemory.hooks.codex_assets import install_shared_skills

            result["skills"] = len(install_shared_skills(home=scope, dry_run=dry_run))
    except Exception as exc:
        result["error"] = str(exc)
        result["mcp_config"] = "error"
    return result
