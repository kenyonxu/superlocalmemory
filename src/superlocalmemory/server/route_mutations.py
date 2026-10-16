# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Authenticated policy boundary for non-ingestion HTTP mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request


@dataclass
class RouteMutationAuthorization:
    """One pre-authorized mutation whose post hook is emitted on success."""

    engine: Any
    operation: str
    context: dict[str, str]
    _completed: bool = False

    @property
    def actor_id(self) -> str:
        return self.context["agent_id"]

    def complete(self) -> None:
        """Record a successful mutation exactly once."""
        if self._completed:
            return
        self.engine._hooks.run_post(self.operation, self.context)
        self._completed = True


def _route_actor(request: Request, principal: dict, profile: str):
    """``(actor, mode)`` for the caller who was just authorized on ``profile``.

    Built from the request's own principal: the machine owner when no session
    was presented, otherwise the signed-in user with their persisted role on
    ``profile``. Company mode is either switch (config.toml or the dashboard's
    "Require login"), or simply a signed-in user. Before 4.1.25 (audit OPS-26)
    this was built from the deployment file alone: a signed-in viewer on a
    personal deployment evaluated as the OWNER, and on an enterprise
    deployment every caller, valid administrators included, as ANONYMOUS.
    """
    from superlocalmemory.core.actor_context import ActorContext, ActorRole, Transport
    from superlocalmemory.core.admission import _company_mode_active, _resolve_deployment
    from superlocalmemory.server.rbac_enforce import (
        company_mode_active,
        has_daemon_capability,
        resolve_actor_roles,
    )

    rbac = getattr(request.app.state, "rbac", None)
    is_user = principal.get("kind") == "user"
    company = (is_user or company_mode_active(rbac)
               or _company_mode_active(_resolve_deployment()))
    roles = resolve_actor_roles(request, profile=profile)
    if not is_user and company and not has_daemon_capability(request):
        # A caller with no session where a login is required is nobody in
        # particular. Only the daemon capability (the command line's
        # break-glass, a file only this computer's user can read) is the owner.
        roles = frozenset({ActorRole.ANONYMOUS})
    actor = ActorContext(
        principal_id=str(principal.get("user_id") or "local-operator"),
        roles=roles,
        active_profile_id=profile,
        transport=Transport.HTTP,
        client_host=getattr(getattr(request, "client", None), "host", "") or "",
    )
    return actor, ("company" if company else "local")


def authorize_route_mutation(
    request: Request,
    *,
    operation: str,
    source_agent_id: str,
    profile_id: str | None = None,
    fact_id: str = "",
    content_preview: str = "",
    permission=None,
    owner_permission=None,
    operation_policy: bool = True,
) -> RouteMutationAuthorization:
    """Authorize the caller on the workspace it changes, then run the pre-hook.

    ``source_agent_id`` is a code-owned route label, never caller identity.
    The security principal comes from middleware or a verified credential, and
    the caller's role is checked on the workspace the change lands in
    (``profile_id``, else the active one): ``write`` for an update, ``delete``
    for a delete, or the ``permission`` a route names (``owner_permission``
    for a caller that presents no session). Then the same
    operation policy MCP uses (correct / forget) is evaluated for that caller,
    unless ``operation_policy`` is False for a route whose role check above is
    its whole policy (entering a workspace).
    """
    if operation not in {"update", "delete"}:
        raise ValueError("route mutations must use update or delete policy")

    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.server.rbac_enforce import _session_token, require_permission
    from superlocalmemory.server.routes.helpers import get_engine_lazy
    from superlocalmemory.server.write_identity import authenticated_request_actor

    app_state = request.app.state
    actor_id = authenticated_request_actor(
        request,
        getattr(app_state, "daemon_descriptor", None),
        actor_kind=source_agent_id,
    )
    engine = get_engine_lazy(app_state)
    if engine is None or not hasattr(engine, "_hooks"):
        raise HTTPException(503, detail="Engine not initialized")

    effective_profile = profile_id or engine.profile_id
    needed = permission or (
        Permission.DELETE if operation == "delete" else Permission.WRITE
    )
    if owner_permission is not None and not _session_token(request):
        needed = owner_permission  # the machine owner, as the mutation gate asks
    principal = require_permission(request, needed, profile=effective_profile)
    context = {
        "operation": operation,
        "agent_id": actor_id,
        "source_agent_id": source_agent_id,
        "profile_id": effective_profile,
    }
    if principal.get("kind") == "user":
        context["user_id"] = str(principal["user_id"])
    if fact_id:
        context["fact_id"] = fact_id
    if content_preview:
        context["content_preview"] = content_preview[:100]

    # Phase-1/E2: run admission registry before trust-hook pre-handler so
    # enterprise policy can deny the mutation even if the trust hook allows it.
    from superlocalmemory.core.admission import AdmissionDenied, OperationKind, admit

    if operation_policy:
        http_actor, mode = _route_actor(request, principal, effective_profile)
        kind = OperationKind.FORGET if operation == "delete" else OperationKind.CORRECT
        try:
            admit(kind, http_actor, mode=mode)
        except AdmissionDenied as exc:
            raise HTTPException(
                403,
                detail=f"Your role does not allow this change ({exc.decision.reason}).",
            ) from exc

    try:
        engine._hooks.run_pre(operation, context)
    except Exception as exc:
        raise HTTPException(403, detail="Write authorization rejected") from exc
    return RouteMutationAuthorization(engine, operation, context)


__all__ = ["RouteMutationAuthorization", "authorize_route_mutation"]
