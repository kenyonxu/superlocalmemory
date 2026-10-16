# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The workspace a daemon mutation acts on, decided once per request.

A request that names a workspace uses it; one that names none uses the
caller's own bound workspace (``caller_workspace.bound_workspace``); only a
caller with neither acts on the engine's workspace, which is the machine
owner's. Handlers that read ``engine.profile_id`` instead ran for everyone
against the shared workspace while the permission gate checked a role that
belongs to somebody else (PR 248 re-check, finding N10).

The handler checks permission against the value returned here, runs against
it, and returns it, so the caller is told which workspace was acted on.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from superlocalmemory.server.caller_workspace import route_to_bound


def acting_profile(engine_profile: str, named: Any = "") -> str:
    """``named`` (stripped), else the caller's bound workspace, else the engine's."""
    explicit = named.strip() if isinstance(named, str) else ""
    return explicit or route_to_bound(engine_profile) or engine_profile


def unknown_profile_response(engine: Any, profile_id: str) -> JSONResponse | None:
    """A 404 when ``profile_id`` is a routed workspace that does not exist.

    Called after the permission gate, so a caller without a role on a workspace
    cannot learn from 404 versus 403 which workspaces exist.
    """
    if profile_id == engine._profile_id:
        return None
    if engine._db.execute("SELECT 1 AS one FROM profiles WHERE profile_id = ?", (profile_id,)):
        return None
    from superlocalmemory.server.routed_profile import unknown_profile_body

    return JSONResponse(unknown_profile_body(profile_id), status_code=404)


__all__ = ["acting_profile", "unknown_profile_response"]
