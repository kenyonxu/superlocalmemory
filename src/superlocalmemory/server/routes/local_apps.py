"""Local MCP connectivity and daemon controls for the dashboard."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from superlocalmemory.infra import mcp_processes
from superlocalmemory.infra.data_root import canonical_data_root
from superlocalmemory.server.daemon_diagnostics import run_doctor
from superlocalmemory.server.loopback import is_loopback
from superlocalmemory.server.rbac_enforce import require_global_manage, require_manage

router = APIRouter(tags=["local-apps"])
PROFILES = [
    {"id": "core", "name": "Memory essentials", "description": "Save, find and correct memories"},
    {"id": "code", "name": "Coding", "description": "Memory, code graph and execution evidence"},
    {
        "id": "full",
        "name": "All everyday tools",
        "description": "Memory, learning, bots and enabled media",
    },
    {
        "id": "power",
        "name": "Administration",
        "description": "Everyday tools plus diagnostics and management",
    },
    {"id": "mesh", "name": "Bot messages", "description": "Coordinate bots without memory tools"},
    {"id": "whole", "name": "Every tool", "description": "The complete available MCP surface"},
]


def _gate(request: Request, *, mutation: bool = False) -> None:
    if not is_loopback(request.client.host if request.client else "") or not is_loopback(
        request.url.hostname or ""
    ):
        raise HTTPException(403, "Local dashboard required")
    if mutation:
        if request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            raise HTTPException(403, "Same-origin dashboard required")
        origin = request.headers.get("Origin")
        if origin and origin != str(request.url).split(request.url.path)[0]:
            raise HTTPException(403, "Same-origin dashboard required")
        from superlocalmemory.server.write_identity import require_write_actor

        require_write_actor(request, getattr(request.app.state, "daemon_descriptor", None))
    if mutation:
        require_global_manage(request)
    else:
        require_manage(request)


def _configured_hosts() -> list[str]:
    """Hosts whose own settings already carry SLM. Best effort: never fails a status."""
    try:
        from superlocalmemory.cli import host_upgrades

        return list(host_upgrades._detected_hosts())
    except Exception:  # noqa: BLE001 - unreadable host settings only mean "unknown"
        return []


def _profile_of(tools: list[str]) -> str | None:
    """The named tool selection a session exposes, or None for a custom set."""
    try:
        from superlocalmemory.mcp.profiles import _PROFILE_DEFINITIONS
    except Exception:  # noqa: BLE001
        return None
    have = frozenset(tools)
    if not have:
        return None
    for name in ("core", "code", "full", "power", "mesh"):
        if _PROFILE_DEFINITIONS.get(name) == have:
            return name
    return None


def _status() -> dict:
    from superlocalmemory.hooks.portable_kit import IDE_MATRIX

    snap = mcp_processes.snapshot(canonical_data_root())
    apps = [
        {**app, "tools_profile": _profile_of(app.get("tools") or [])}
        for app in snap.get("apps", [])
    ]
    configured = set(_configured_hosts())
    live = {}
    for app in apps:
        live[app["host"]] = live.get(app["host"], 0) + 1
    available = [
        {"id": key, "name": desc.display}
        for key, desc in IDE_MATRIX.items()
        if desc.fmt and desc.mcp_path_global and key != "vscode-copilot"
    ]
    available += [{"id": "claude-code", "name": "Claude Code"}, {"id": "hermes", "name": "Hermes"}]
    for row in available:
        row["configured"] = row["id"] in configured
        row["connected"] = live.get(row["id"], 0) > 0
    names = {row["id"]: row["name"] for row in available}
    return {
        **snap,
        "apps": apps,
        "available_hosts": available,
        "configured_hosts": [
            {
                "id": host,
                "name": mcp_processes.HOST_NAMES.get(host) or names.get(host, host),
                "live": live.get(host, 0),
            }
            for host in sorted(configured)
        ],
        "profiles": PROFILES,
    }


@router.get("/api/v3/local-apps")
async def local_apps(request: Request):
    _gate(request)
    return await asyncio.to_thread(_status)


class ConnectIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host: str
    tools_profile: str = "code"


def _connect(intent: ConnectIntent) -> bool:
    from superlocalmemory.cli.deployment import runtime_port
    from superlocalmemory.hooks.portable_kit import connect_ide
    from superlocalmemory.server.local_host_setup import configure_claude_code, configure_hermes

    if intent.host == "hermes":
        return configure_hermes(intent.tools_profile)
    if intent.host == "claude-code":
        return configure_claude_code(intent.tools_profile)
    result = connect_ide(intent.host, profile=intent.tools_profile, daemon_port=runtime_port())
    if result.get("error"):
        raise ValueError("The app settings could not be updated. Check app permissions and retry.")
    return result.get("mcp_config") in {"wrote", "merged", "unchanged"}


@router.post("/api/v3/local-apps/connect")
async def connect_local_app(request: Request, intent: ConnectIntent):
    _gate(request, mutation=True)
    status = _status()
    if intent.host not in {row["id"] for row in status["available_hosts"]}:
        raise HTTPException(400, "Choose a supported local app")
    if intent.tools_profile not in {row["id"] for row in PROFILES}:
        raise HTTPException(400, "Choose an available tools selection")
    try:
        success = await asyncio.to_thread(_connect, intent)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "App setup could not be confirmed. Retry setup.") from None
    if not success:
        raise HTTPException(409, "App setup was not confirmed. Refresh and retry.")
    return {
        "success": True,
        "connected": False,
        "message": "Settings saved. Open or restart this app to establish its MCP connection.",
    }


@router.post("/api/v3/local-apps/{session}/reconnect")
async def reconnect_local_app(request: Request, session: str):
    _gate(request, mutation=True)
    try:
        requested = await asyncio.to_thread(
            mcp_processes.request_reconnect, canonical_data_root(), session
        )
    except ValueError:
        raise HTTPException(400, "Invalid connection") from None
    if not requested:
        raise HTTPException(404, "This app session is no longer connected")
    return {
        "requested": True,
        "message": "Connection will close. Reopen the app's MCP connection or restart the app.",
    }


async def _diagnose(request: Request, *, repair: bool) -> dict:
    _gate(request, mutation=True)
    lock = getattr(request.app.state, "connectivity_diagnostics_lock", None)
    if lock is None:
        lock = request.app.state.connectivity_diagnostics_lock = asyncio.Lock()
    if lock.locked():
        raise HTTPException(409, "A check or repair is already running")
    async with lock:
        return await run_doctor(repair=repair)


@router.post("/api/daemon/doctor")
async def doctor(request: Request):
    return await _diagnose(request, repair=False)


@router.post("/api/daemon/repair")
async def repair(request: Request):
    return await _diagnose(request, repair=True)
