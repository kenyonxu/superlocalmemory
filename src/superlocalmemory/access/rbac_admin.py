# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Account administration that leaves evidence, and stays inside its authority.

Two 4.1.25 audit findings live here.

**OPS-21: access-control events reach the hash-chained audit trail.** The
documentation promised that every user, role, login, session and membership
event is in the chain; none was. Every administrative change now writes a
``requested`` record before it is applied and an ``ok``/``refused`` record
after it. If the ``requested`` record cannot be written the change is not made
(:class:`AuditUnavailable`, answered as 503): an administrative change with no
evidence is refused, not made quietly. Sign-in, sign-out and session ending are
recorded best-effort (an unreadable audit store must not lock every person
out); a failure there is logged as an error. Records carry who acted, on whom,
on which workspace, the old and new role, and the outcome. Never a password, a
token, or memory content.

**OPS-27: a workspace administrator administers memberships, not identities.**
A password, the account's status and its deletion are global: the same account
may be an administrator of another workspace. An administrator of ``default``
used to be able to reset the password of an account that also administers
``sales``, then sign in as it. Now such a change needs the computer's owner,
the person themselves (a password change then proves the current password), or
an administrator of *every* workspace the account belongs to.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from superlocalmemory.access.rbac import Permission, RbacEngine, RbacError

logger = logging.getLogger("superlocalmemory.access.rbac_admin")

AUDIT_PREFIX = "access."


class AuditUnavailable(RuntimeError):
    """The audit trail could not record an administrative change, so it was not made."""


class AuthorityError(RbacError):
    """The caller may not change this account."""


def _audit_path(rbac: RbacEngine) -> Path:
    # The engine's audit post-hooks write next to memory.db (engine_wiring).
    return Path(rbac._db_path).parent / "audit_chain.db"


def record(rbac: RbacEngine, event: str, *, actor: str, profile: str = "",
           strict: bool = False, **details: Any) -> bool:
    """Append one access-control event. ``strict`` raises when it cannot."""
    from superlocalmemory.compliance.audit import AuditChain

    safe = {k: v for k, v in details.items() if v is not None}
    # No workspace = the whole installation (accounts, the login policy,
    # sign-in): say so, so the audit pane can show it to whoever holds
    # authority over the installation (PR 248 review, finding 5).
    safe["scope"] = "workspace" if profile else "installation"
    try:
        AuditChain(_audit_path(rbac)).log(
            operation=AUDIT_PREFIX + event, agent_id=actor, profile_id=profile,
            metadata=safe,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - reported per the strict flag
        logger.error("access audit: %s could not be recorded: %s", event, exc)
        if strict:
            raise AuditUnavailable(
                "The audit trail could not record this change, so it was not made."
            ) from exc
        return False


def actor_of(principal: dict) -> str:
    """The audit name of a caller: ``user:<id>`` or ``owner``."""
    if principal.get("kind") == "user":
        return f"user:{principal['user_id']}"
    return "owner"


def audited(rbac: RbacEngine, event: str, principal: dict, action, *,
            profile: str = "", **details: Any):
    """Run ``action()`` between a ``requested`` and an outcome record."""
    actor = actor_of(principal)
    record(rbac, event, actor=actor, profile=profile, strict=True,
           outcome="requested", **details)
    try:
        result = action()
    except RbacError as exc:
        record(rbac, event, actor=actor, profile=profile, outcome="refused",
               reason=type(exc).__name__, **details)
        raise
    record(rbac, event, actor=actor, profile=profile, outcome="ok", **details)
    return result


def check_identity_authority(rbac: RbacEngine, principal: dict, target_user_id: str,
                             *, field: str, current_password: str | None = None) -> None:
    """Raise :class:`AuthorityError` unless the caller may change this account.

    ``field`` is ``password``, ``status``, ``display_name`` or ``delete``.
    """
    if principal.get("kind") != "user":
        return  # the computer's owner (already proved by require_manage)
    caller = principal["user_id"]
    if caller == target_user_id:
        _check_self_service(rbac, principal, field, current_password)
        return
    memberships = [row["profile_id"] for row in rbac.list_user_profiles(target_user_id)]
    if memberships and all(rbac.has_permission(caller, p, Permission.MANAGE)
                           for p in memberships):
        return
    raise AuthorityError(
        "This account also has access to workspaces you do not manage, so only "
        "the computer's owner or the person themselves can change it. You can "
        "still change or remove its role on the workspaces you manage."
    )


def _check_self_service(rbac: RbacEngine, principal: dict, field: str,
                        current_password: str | None) -> None:
    if field == "display_name":
        return
    if field == "password":
        if current_password and rbac.verify_credentials(principal["username"], current_password):
            return
        raise AuthorityError("To change your own password, enter your current password.")
    raise AuthorityError("You cannot disable or delete your own account. Ask an administrator.")


__all__ = [
    "AUDIT_PREFIX",
    "AuditUnavailable",
    "AuthorityError",
    "actor_of",
    "audited",
    "check_identity_authority",
    "record",
]
