# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Ingestion adapter management API — enable/disable/start/stop from dashboard.

v3.4.4: Users can manage Gmail, Calendar, Transcript adapters entirely from
the dashboard UI — no CLI needed. The best product experience.

Endpoints:
  GET  /api/adapters         — list all adapters with status
  POST /api/adapters/enable  — enable an adapter
  POST /api/adapters/disable — disable an adapter
  POST /api/adapters/start   — start a running adapter
  POST /api/adapters/stop    — stop a running adapter
  POST /api/adapters/configure — choose the file or folder an importer reads
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["adapters"])


class AdapterAction(BaseModel):
    name: str


class AdapterSetup(AdapterAction):
    path: str


@router.post("/api/adapters/configure")
async def configure_adapter_api(body: AdapterSetup, request: Request):
    """Point an importer at a file or folder inside an import folder.

    Installation-wide MANAGE: the importer reads local files as the daemon's
    user and turns them into memories, so a workspace administrator (possibly
    on another computer) must not choose what it reads. The path must resolve
    inside ``<data root>/imports`` or a folder the owner approved on this
    computer (``ingestion.import_roots``).
    """
    from fastapi import HTTPException

    from superlocalmemory.ingestion import import_roots
    from superlocalmemory.ingestion.adapter_manager import _load_config, _save_config, _slm_home
    from superlocalmemory.server import rbac_enforce

    rbac_enforce.require_global_manage(request)
    if body.name not in ("gmail", "calendar", "transcript"):
        raise HTTPException(422, "Choose a supported adapter.")
    try:
        path = import_roots.admit(body.path, _slm_home())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.name == "transcript" and not path.is_dir():
        raise HTTPException(422, "Transcripts need a folder.")
    if body.name != "transcript" and not path.is_file():
        raise HTTPException(422, "Choose an MBOX or ICS file.")
    field = {"transcript": "watch_dir", "gmail": "mbox_path", "calendar": "ics_path"}[body.name]
    config = _load_config()
    config.setdefault(body.name, {}).update({field: str(path), "tier": "auto"})
    _save_config(config)
    return {"ok": True, "message": "Setup saved. Retry Start."}


#: Fields of an adapter row that name a local file or folder.
_PATH_FIELDS = ("watch_dir", "mbox_path", "ics_path")


@router.get("/api/adapters")
def list_adapters_api(request: Request):
    """List all adapters with their enabled/running status.

    Any reader gets the cards. Configured paths are local filesystem details,
    shown only to a caller with installation-wide MANAGE (the people who may
    change them); everyone else gets them blank and ``paths_hidden``.
    """
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.ingestion.adapter_manager import list_adapters
    from superlocalmemory.server import rbac_enforce

    rbac_enforce.require_permission(request, Permission.READ)
    adapters = list_adapters()
    hidden = not rbac_enforce.has_global_manage(request)
    if hidden:
        adapters = [{**row, **{f: "" for f in _PATH_FIELDS if f in row}} for row in adapters]
    return {"adapters": adapters, "paths_hidden": hidden}


@router.post("/api/adapters/enable")
async def enable_adapter_api(body: AdapterAction):
    """Enable an adapter (doesn't start it yet)."""
    from superlocalmemory.ingestion.adapter_manager import enable_adapter
    return enable_adapter(body.name)


@router.post("/api/adapters/disable")
async def disable_adapter_api(body: AdapterAction):
    """Disable and stop an adapter."""
    from superlocalmemory.ingestion.adapter_manager import disable_adapter
    return disable_adapter(body.name)


@router.post("/api/adapters/start")
async def start_adapter_api(body: AdapterAction):
    """Start a running adapter subprocess."""
    from superlocalmemory.ingestion.adapter_manager import start_adapter
    return start_adapter(body.name)


@router.post("/api/adapters/stop")
async def stop_adapter_api(body: AdapterAction):
    """Stop a running adapter."""
    from superlocalmemory.ingestion.adapter_manager import stop_adapter
    return stop_adapter(body.name)
