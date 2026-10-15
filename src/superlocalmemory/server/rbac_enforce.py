# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 — RBAC / teams (C3)

"""RBAC enforcement boundary for HTTP routes.

This is the single place that turns "who is calling" + "what are they trying to
do" into an allow/deny decision, on top of the existing machine-auth layer
(write_identity). It is deliberately small so every mutation route calls the
same code path — the research warning was explicit: an RBAC layer that is
defined but not consistently called is worse than none.

Principal model
---------------
* **user**  — a logged-in dashboard user (valid session token). Always enforced
  against their role on the active profile.
* **owner** — the machine operator (already proved machine auth via
  write_identity; no user session). In personal mode the owner bypasses RBAC
  (all permissions). When the org turns on *require_login* (company mode) the
  owner bypass is disabled for data and a user session is mandatory. The owner
  keeps MANAGE only through the daemon capability (a 0600 file that is never
  served over HTTP: the CLI break-glass for a locked-out admin). The install
  token and an API key are NOT authority to administer: the install token is
  handed to any loopback caller by ``GET /internal/token`` and an API key
  reaches the LAN, so either one alone would let anyone switch company mode
  off and read everyone's data. Administration then needs a signed-in user who
  holds MANAGE. A session token that does not resolve is refused (401) rather
  than treated as the owner. With no users enrolled yet the owner is still the
  machine, so the first administrator can be created.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from superlocalmemory.access.rbac import Permission, Role, permissions_for_role

logger = logging.getLogger(__name__)

_SESSION_HEADER = "X-SLM-User-Session"
_SESSION_COOKIE = "slm_session"

OWNER_PRINCIPAL = {
    "kind": "owner",
    "user_id": "owner",
    "username": "owner",
    "display_name": "Machine Owner",
}


def get_rbac_engine(app_state: Any) -> Any | None:
    return getattr(app_state, "rbac", None)


def _session_token(request: Request) -> str:
    tok = request.headers.get(_SESSION_HEADER, "")
    if tok:
        return tok
    try:
        return request.cookies.get(_SESSION_COOKIE, "") or ""
    except Exception:
        return ""


def has_machine_credential(request: Request) -> bool:
    """True when the caller presents a valid machine credential.

    Daemon capability, install token (loopback only) or API key. A bare
    loopback peer with no credential is NOT a machine credential.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    try:
        require_write_actor(
            request,
            getattr(request.app.state, "daemon_descriptor", None),
            actor_kind="rbac-owner",
        )
    except HTTPException:
        return False
    return True


def has_daemon_capability(request: Request) -> bool:
    """True when the caller presents this daemon's private capability.

    The capability lives in a 0600 file next to the daemon and is never served
    over HTTP, so holding it proves access to this user's files.
    """
    from superlocalmemory.server.write_identity import require_daemon_actor

    try:
        require_daemon_actor(request, getattr(request.app.state, "daemon_descriptor", None))
    except HTTPException:
        return False
    return True


def company_mode_active(rbac: Any | None) -> bool:
    """Company mode: users are enrolled and the workspace requires login."""
    try:
        return bool(rbac is not None and rbac.require_login() and rbac.user_count() > 0)
    except Exception:  # noqa: BLE001 - an unreadable policy is treated as strict
        return rbac is not None


def require_machine_credential(request: Request) -> None:
    """Reject (403) a caller that holds no valid machine credential."""
    if not has_machine_credential(request):
        raise HTTPException(
            403,
            detail=(
                "This workspace requires login: administration needs the "
                "local install token, the daemon capability or an API key."
            ),
        )


INVALID_SESSION_MESSAGE = (
    "Your session has ended (signed out, expired or revoked). Sign in again, "
    "or remove the old session to continue as this computer's owner."
)


def _accounts_exist(rbac: Any) -> bool:
    """Whether any team account exists. Unreadable counts as yes (fail closed)."""
    try:
        return rbac.user_count() > 0
    except Exception:  # noqa: BLE001 - an unreadable store is not "no accounts"
        return True


def presented_session_user(rbac: Any | None, token: str) -> tuple[dict | None, bool]:
    """``(user, refused)`` for a session token a caller presented.

    ``refused`` is True when a token was presented, team accounts exist and the
    token names nobody (signed out, expired, revoked, disabled, forged). That
    caller once held, or claims, a user's limited role; it must never fall
    back to the machine owner, in any mode (4.1.25 audit OPS-24: a viewer's
    revoked session became the owner on its next request when login was
    optional). With no accounts at all a stray token is ignored, so a single
    operator with an old cookie is unchanged.
    """
    if rbac is None or not token:
        return None, False
    user = rbac.resolve_session(token)
    if user:
        return user, False
    return None, _accounts_exist(rbac)


def resolve_principal(request: Request) -> dict:
    """Resolve the caller to a user (valid session) or the machine owner.

    A session token that does not resolve (expired, revoked, forged) is
    refused with 401 in every mode once team accounts exist; it never silently
    becomes the owner. Only a caller that presents no session is the owner.
    """
    rbac = get_rbac_engine(request.app.state)
    user, refused = presented_session_user(rbac, _session_token(request))
    if user:
        return {"kind": "user", **user}
    if refused:
        raise HTTPException(401, detail=INVALID_SESSION_MESSAGE)
    return dict(OWNER_PRINCIPAL)


def _active_profile() -> str:
    from superlocalmemory.server.routes.helpers import get_active_profile

    return get_active_profile()


def require_permission(
    request: Request,
    permission: Permission,
    *,
    profile: str | None = None,
) -> dict:
    """Authorize ``permission`` on ``profile`` (default: active profile).

    Returns the principal on success. Raises 401 when a login is required but
    absent, or 403 when the user's role does not grant the permission.
    """
    rbac = get_rbac_engine(request.app.state)
    principal = resolve_principal(request)
    require_login = bool(rbac is not None and rbac.require_login())
    prof = profile or _active_profile()

    if principal["kind"] == "owner":
        # In company mode the owner keeps MANAGE only through the daemon
        # capability (command-line break-glass): "no session" alone is any
        # local process, and the install token / an API key are reachable by
        # one. require_login also gates the owner's DATA operations.
        if require_login:
            if permission != Permission.MANAGE:
                raise HTTPException(
                    401,
                    detail=(
                        "Login required: this workspace enforces per-user "
                        "access."
                    ),
                )
            if company_mode_active(rbac):
                if not has_daemon_capability(request):
                    raise HTTPException(
                        403,
                        detail=(
                            "Administration of a workspace that requires login "
                            "needs a signed-in administrator, or the daemon "
                            "capability from the command line."
                        ),
                    )
            else:
                require_machine_credential(request)  # first administrator
        return principal  # personal mode — operator is owner

    # Logged-in user: always enforced against their role.
    if rbac is not None and rbac.has_permission(principal["user_id"], prof, permission):
        return principal
    raise HTTPException(
        403,
        detail=(
            f"Your role does not allow '{permission.value}' on this workspace."
        ),
    )


def require_manage(request: Request, *, profile: str | None = None) -> dict:
    """Guard for user/role administration (MANAGE permission)."""
    return require_permission(request, Permission.MANAGE, profile=profile)


GLOBAL_MANAGE_MESSAGE = (
    "Your role does not allow changing this setting: it applies to every "
    "workspace on this computer, so only the computer's owner or an "
    "administrator of every workspace can change it."
)


def installation_workspaces(rbac: Any) -> frozenset[str]:
    """Every workspace on this installation: those with members and those stored.

    A workspace nobody belongs to still counts: switching company mode off,
    for one, would open it to anyone. An unreadable profile list adds nothing
    (the member list alone still decides), never a reason to say yes.
    """
    spaces = set(rbac.list_member_workspaces())
    try:
        from superlocalmemory.server.routes.helpers import _get_db_profiles

        spaces.update(str(p["profile_id"]) for p in _get_db_profiles() if p.get("profile_id"))
    except Exception:  # noqa: BLE001 - the membership list is the floor
        logger.debug("installation workspaces: profile list unreadable", exc_info=True)
    return frozenset(spaces)


def holds_global_manage(rbac: Any, user_id: str) -> bool:
    """True when ``user_id`` administers every workspace on this installation."""
    if rbac is None:
        return False
    spaces = installation_workspaces(rbac)
    return bool(spaces) and all(
        rbac.has_permission(user_id, space, Permission.MANAGE) for space in spaces
    )


def require_global_manage(request: Request) -> dict:
    """Authorize a change to installation-wide state (one setting for everyone).

    The policy that turns login on or off, the Jev consent and key, the
    provider, the embedding model, Optimize, restarts: none of these belongs
    to one workspace, so MANAGE on the active one is not enough (4.1.25 PR 248
    review, finding 1: an administrator of ``sales`` switched company mode off
    for ``default``). Allowed: the machine owner under the same rules as any
    MANAGE (personal mode, or the daemon capability in company mode), or a
    signed-in user who administers every workspace.
    """
    principal = resolve_principal(request)
    if principal["kind"] != "user":
        return require_permission(request, Permission.MANAGE)
    rbac = get_rbac_engine(request.app.state)
    if holds_global_manage(rbac, principal["user_id"]):
        return principal
    raise HTTPException(403, detail=GLOBAL_MANAGE_MESSAGE)


def has_global_manage(request: Request) -> bool:
    """Whether the caller holds installation-wide authority (never raises)."""
    try:
        require_global_manage(request)
    except HTTPException:
        return False
    except Exception:  # noqa: BLE001 - authority that cannot be read is not held
        logger.warning("installation authority could not be read", exc_info=True)
        return False
    return True


def resolve_actor_roles(request: Request, *, profile: str | None = None):
    """Resolve the caller to concrete ActorContext roles (server-derived).

    The machine operator (owner) is root. A logged-in user is mapped from their
    persisted RBAC role on ``profile``. This must be called only after
    ``require_permission`` has already authorized the operation, so the returned
    role always includes the permission the caller was admitted with.
    """
    from superlocalmemory.core.actor_context import ActorRole

    principal = resolve_principal(request)
    if principal.get("kind") == "owner":
        return frozenset({ActorRole.OWNER})
    rbac = get_rbac_engine(request.app.state)
    role = None
    if rbac is not None:
        try:
            role = rbac.get_role(principal["user_id"], profile or _active_profile())
        except Exception as exc:  # noqa: BLE001
            # A lookup that failed is not a lookup that said yes.
            #
            # This used to return MEMBER, on the reasoning that the caller had
            # already passed a coarser permission check so a transient database
            # error should not deny an authorised write. The effect was that any
            # error in the role lookup -- a write-lock timeout, a checkpoint, a
            # corrupt page -- promoted a viewer to a role that can write, at
            # exactly the moment the store was under stress. A caller able to
            # provoke lock contention could provoke the promotion.
            #
            # "Ask again in a moment" is the honest answer and the one the
            # caller can act on. It is neither a denial nor a grant.
            from fastapi import HTTPException

            logger.warning(
                "rbac: the role for this caller could not be read (%s); "
                "answering 503 rather than assuming one", exc,
            )
            raise HTTPException(
                status_code=503,
                detail="the workspace's roles are temporarily unreadable; retry",
            ) from exc
    mapped = {
        Role.ADMIN: ActorRole.ADMIN,
        Role.MEMBER: ActorRole.MEMBER,
        Role.VIEWER: ActorRole.VIEWER,
    }.get(role)
    return frozenset({mapped}) if mapped is not None else frozenset({ActorRole.ANONYMOUS})


def principal_info(request: Request) -> dict:
    """Rich identity for /whoami: principal + role + effective permissions on
    the active profile. Never raises — used by the dashboard to render UI."""
    rbac = get_rbac_engine(request.app.state)
    session_invalid = False
    try:
        principal = resolve_principal(request)
    except HTTPException:
        # An expired session: the dashboard asks who it is before it shows the
        # login form. Report the unauthenticated view and say the session is
        # over, so the page can drop it (every other request is refused).
        principal = dict(OWNER_PRINCIPAL)
        session_invalid = True
    prof = _active_profile()
    info = {
        "session_invalid": session_invalid,
        "kind": principal["kind"],
        "user_id": principal["user_id"],
        "username": principal["username"],
        "display_name": principal.get("display_name", principal["username"]),
        "profile": prof,
        "rbac_active": bool(rbac is not None and rbac.user_count() > 0),
        "require_login": bool(rbac is not None and rbac.require_login()),
    }
    if principal["kind"] == "owner":
        # Owner has every permission (personal mode) unless login is required.
        info["role"] = "owner"
        # A logged-out owner of a company workspace can do nothing from here.
        company = company_mode_active(rbac)
        info["permissions"] = (
            [] if company and not has_daemon_capability(request)
            else [p.value for p in Permission]
        )
        return info
    role = rbac.get_role(principal["user_id"], prof) if rbac is not None else None
    info["role"] = role.value if role else None
    info["permissions"] = (
        [p.value for p in permissions_for_role(role)] if role else []
    )
    return info
