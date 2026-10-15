"""Host discovery and installation share the portable registry."""

from __future__ import annotations

import sys
from pathlib import Path

from superlocalmemory.hooks import portable_kit as kit


def host_inventory(home: Path | None = None) -> list[dict]:
    home = home or Path.home()
    from superlocalmemory.hooks.codex_assets import AGENTS, SKILLS

    result = []
    for name, desc in kit.IDE_MATRIX.items():
        path = kit.global_config_path(desc, home) if desc.fmt else None
        result.append(
            {
                "ide_id": name,
                "name": desc.display,
                "config_path": str(path) if path else None,
                "installed": bool(path and path.exists()),
                "install_method": "mcp-config" if desc.fmt else "native-plugin",
                "transports": sorted(kit.VALID_TRANSPORTS) if desc.fmt else [],
                "host_verification": "pending",
                "learning_hooks": "not-verified",
                "asset_counts": {"skills": len(SKILLS), "agents": len(AGENTS)}
                if name == "codex"
                else None,
            }
        )
    return result


def _emit(args, result):
    if getattr(args, "json", False):
        from superlocalmemory.cli.json_output import json_print

        json_print("connect", data=result)
    elif "ides" in result:
        for host in result["ides"]:
            print(
                f"  {host['ide_id']:22s} {host['install_method']:14s} {host['config_path'] or ''}"
            )
    elif result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
    else:
        print(f"{result['ide']}: mcp_config={result['mcp_config']} path={result['mcp_path']}")
        if result.get("instructions"):
            print(result["instructions"])
        print(
            "Configuration written; host verification pending."
            if result["mcp_config"] in {"wrote", "merged", "unchanged"}
            else result["mcp_config"]
        )
    verification = result.get("mcp_verification")
    if verification and not getattr(args, "json", False):
        if verification["verified"]:
            print(f"MCP verified: initialize/list_tools ({len(verification['tools'])} tools).")
        else:
            print(f"MCP verification failed: {verification['error']}", file=sys.stderr)
    if result.get("error"):
        raise SystemExit(1)


def _connect_one(args, ide):
    from superlocalmemory.cli.commands import _agents_md_source_factory
    from superlocalmemory.cli.deployment import runtime_port

    daemon_port = getattr(args, "daemon_port", None) or runtime_port()

    result = kit.connect_ide(
        ide,
        project=Path.cwd(),
        here=getattr(args, "here", False),
        profile=getattr(args, "profile", None),
        agents_md_source=_agents_md_source_factory(),
        dry_run=getattr(args, "dry_run", False),
        transport=getattr(args, "transport", "stdio"),
        daemon_port=daemon_port,
    )
    # The requested host is the fallback name for output and diagnostics.
    result = {"ide": ide, **result}
    if (
        getattr(args, "verify", False)
        and not result.get("error")
        and not getattr(args, "dry_run", False)
    ):
        if getattr(args, "transport", "stdio") != "stdio":
            from superlocalmemory.hooks.verify_mcp import verify_http_mcp

            result["mcp_verification"] = verify_http_mcp(
                daemon_port,
                profile=getattr(args, "profile", None),
            )
    if (
        not result.get("error")
        and not getattr(args, "dry_run", False)
        and result["mcp_config"] in {"wrote", "merged", "unchanged"}
    ):
        from superlocalmemory.infra.local_diagnostics import record_operation

        record_operation("activation", client=result["ide"])
    return result


def cmd_connect(args):
    if getattr(args, "disable", None) or getattr(args, "cross_platform", False):
        from superlocalmemory.cli.context_commands import cmd_connect_cross_platform

        return cmd_connect_cross_platform(args)
    ide = getattr(args, "ide", None)
    if ide:
        return _emit(args, _connect_one(args, ide))
    if getattr(args, "list", False):
        return _emit(args, {"ides": host_inventory()})
    targets = [h["ide_id"] for h in host_inventory() if h["installed"]]
    results = [_connect_one(args, target) for target in targets]
    _emit(args, {"ides": host_inventory(), "results": results})
