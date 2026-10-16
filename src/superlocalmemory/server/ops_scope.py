# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Who may inspect and remediate which failed operations (4.1.25 audit OPS-22).

Workspace administration is scoped. Holding MANAGE on one workspace used to be
enough to list every workspace's failures (with their error text) and to
cancel or retry any of them by id, because both endpoints checked MANAGE on the
active workspace only and then acted on whatever the caller named.

* The installation owner (no session, personal mode; or the daemon capability
  where a login is required) sees and remediates everything.
* A signed-in user sees only the workspaces they manage, and may remediate an
  operation only when they manage every workspace that holds a record of it.
  An operation in a workspace they do not manage is "not found", the same as
  one that does not exist.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from superlocalmemory.access.rbac import Permission
from superlocalmemory.server.rbac_enforce import (
    get_rbac_engine,
    require_permission,
    resolve_principal,
)

_NOT_FOUND = "Operation not found in a workspace you manage."


def _managed_by(request: Request, user_id: str) -> set[str]:
    rbac = get_rbac_engine(request.app.state)
    if rbac is None:
        return set()
    return {row["profile_id"] for row in rbac.list_user_profiles(user_id)
            if rbac.has_permission(user_id, row["profile_id"], Permission.MANAGE)}


def listing_scope(request: Request, profile: str | None) -> set[str] | None:
    """The workspaces this caller may list failures for (``None`` = all)."""
    if profile:
        require_permission(request, Permission.MANAGE, profile=profile)
        return {profile}
    principal = resolve_principal(request)
    if principal.get("kind") != "user":
        require_permission(request, Permission.MANAGE)  # owner; capability in company mode
        return None
    managed = _managed_by(request, principal["user_id"])
    if not managed:
        raise HTTPException(403, detail="Your role does not allow 'manage' on any workspace.")
    return managed


def remediation_scope(request: Request, db_path: Path, operation_id: str) -> set[str] | None:
    """The workspaces a remediation of ``operation_id`` may touch (``None`` = all).

    Raises 404 when the operation is unknown or lies outside what the caller
    manages, and 503 when its workspace cannot be read (unknown is not "none").
    """
    from superlocalmemory.core.ops_inspection import operation_profiles

    principal = resolve_principal(request)
    if principal.get("kind") != "user":
        require_permission(request, Permission.MANAGE)
    try:
        owners = operation_profiles(db_path, operation_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            503, detail="The operation's workspace could not be read; retry.") from exc
    if principal.get("kind") != "user":
        return None
    if not owners or not owners <= _managed_by(request, principal["user_id"]):
        raise HTTPException(404, detail=_NOT_FOUND)
    return owners


def actor_label(request: Request) -> str:
    """Who did it, from the server's own view of the caller (never a header)."""
    try:
        principal = resolve_principal(request)
    except HTTPException:
        return "unknown"
    if principal.get("kind") == "user":
        return f"user:{principal['user_id']}"
    return str(getattr(request.state, "authenticated_actor", "") or "owner")


__all__ = ["actor_label", "listing_scope", "remediation_scope"]
