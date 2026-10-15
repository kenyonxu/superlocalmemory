# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Which workspace a request means, decided once per request, per caller.

The active workspace used to be one daemon-wide value that any signed-in
reader could move. One person's switch then changed every other person's
authorization: a viewer of ``default`` was refused its own workspace the
moment someone else entered ``sales`` (PR 248 review, finding 2, a
one-request denial of service and the enabling step of finding 1).

Now a signed-in person has their own active workspace, bound to their
session. Their switch moves only them. The daemon-wide value is the machine
owner's (no session, personal mode, or the daemon capability) and is
unchanged. A request that names its workspace (``?profile_id=``, a routed
save) still uses the one it names.

The value is resolved once, before any gate, and pinned to the request: the
gates and the handler read the same value, so a switch landing between them
cannot serve a workspace the gate never checked. A caller without a binding
is the machine owner, whose authorization does not depend on the workspace
(personal mode: everything; company mode: administration with the daemon
capability, never data), so it keeps the existing behaviour of acting on the
generation it is admitted under.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Where the pin lives in the ASGI scope's ``state``.
PIN_KEY = "caller_workspace"
_MAX_BOUND_SESSIONS = 10_000
_SESSION_HEADER = b"x-slm-user-session"
_SESSION_COOKIE = "slm_session"
_OWNER_COOKIE = "slm_mcp_workspace"
_OWNER_KEY = "mcp_owner_workspace"
_OWNER_PRESENTED_KEY = "mcp_owner_binding_presented"
_OWNER_BINDING: contextvars.ContextVar[str] = contextvars.ContextVar(
    "slm_mcp_owner_binding", default="",
)
_OWNER_PRESENTED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "slm_mcp_owner_binding_presented", default=False,
)
_REQUEST_STATE: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "slm_workspace_state", default=None,
)


@dataclass(frozen=True)
class CallerWorkspace:
    """The workspace one request acts on; ``bound`` = the caller's own choice."""

    profile_id: str
    bound: bool


class SessionWorkspaces:
    """Each signed-in session's active workspace (in memory, bounded, LRU)."""

    def __init__(self, limit: int = _MAX_BOUND_SESSIONS) -> None:
        self._lock = threading.Lock()
        self._chosen: OrderedDict[str, str] = OrderedDict()
        self._limit = limit

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def get(self, token: str) -> str | None:
        key = self._key(token)
        with self._lock:
            chosen = self._chosen.get(key)
            if chosen is not None:
                self._chosen.move_to_end(key)
            return chosen

    def bind(self, token: str, profile_id: str) -> None:
        key = self._key(token)
        with self._lock:
            self._chosen[key] = profile_id
            self._chosen.move_to_end(key)
            while len(self._chosen) > self._limit:
                self._chosen.popitem(last=False)

    def forget(self, token: str) -> None:
        with self._lock:
            self._chosen.pop(self._key(token), None)


def session_workspaces(app_state: Any) -> SessionWorkspaces:
    store = getattr(app_state, "session_workspaces", None)
    if store is None:
        store = SessionWorkspaces()
        app_state.session_workspaces = store
    return store


def session_token_from_scope(scope: dict) -> str:
    """The session a request presents (header first, then the cookie)."""
    cookie = ""
    for name, value in scope.get("headers") or ():
        if name == _SESSION_HEADER and value:
            return value.decode("latin-1")
        if name == b"cookie":
            cookie = value.decode("latin-1")
    for part in cookie.split(";"):
        key, _, val = part.strip().partition("=")
        if key == _SESSION_COOKIE and val:
            return val
    return ""


def daemon_workspace(app_state: Any) -> str:
    """The machine owner's (daemon-wide) active workspace."""
    from superlocalmemory.server.profile_runtime import get_profile_runtime

    try:
        return get_profile_runtime(app_state).snapshot.profile_id
    except Exception:  # noqa: BLE001 - no runtime bound (tests, early start)
        from superlocalmemory.server.routes.helpers import get_active_profile

        return get_active_profile()


def _first_readable(rbac: Any, user_id: str, preferred: str) -> str | None:
    from superlocalmemory.access.rbac import Permission

    if rbac.has_permission(user_id, preferred, Permission.READ):
        return preferred
    for space in sorted(row["profile_id"] for row in rbac.list_user_profiles(user_id)):
        if rbac.has_permission(user_id, space, Permission.READ):
            return space
    return None


def _owner_binding(scope: dict, app_state: Any) -> str:
    """A personal HTTP client's workspace choice; this is never authentication."""
    import re
    from http.cookies import SimpleCookie
    from secrets import token_urlsafe

    from superlocalmemory.server.forwarded_guard import has_forwarding_header
    from superlocalmemory.server.remote_access import is_trusted_local_peer

    if not is_trusted_local_peer(scope) or has_forwarding_header(scope.get("headers")):
        return ""

    if session_token_from_scope(scope):
        return ""
    rbac = getattr(app_state, "rbac", None)
    if rbac is not None and rbac.require_login():
        return ""
    headers = dict(scope.get("headers") or ())
    binding = headers.get(b"x-slm-workspace-binding", b"").decode("latin-1")
    # Stateful MCP clients already return the transport-issued session ID,
    # including fetch-based clients without cookie jars. Keep its namespace
    # distinct and forward the derived binding through nested daemon calls.
    transport_session = headers.get(b"mcp-session-id", b"").decode("latin-1")
    if not binding and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", transport_session):
        binding = hashlib.sha256(("mcp:" + transport_session).encode()).hexdigest()
    if not binding:
        jar = SimpleCookie()
        jar.load(headers.get(b"cookie", b"").decode("latin-1"))
        cookie = jar.get(_OWNER_COOKIE)
        binding = cookie.value if cookie is not None else ""
    if binding and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", binding):
        binding = ""
    scope.setdefault("state", {})[_OWNER_PRESENTED_KEY] = bool(binding)
    if not binding and str(scope.get("path", "")).startswith("/mcp"):
        binding = token_urlsafe(32)
    return binding if len(binding) <= 128 else ""


def current_owner_workspace_binding() -> str:
    """Forward routing to nested daemon calls without granting any permission."""
    return _OWNER_BINDING.get()


def resolve_caller_workspace(scope: dict, app_state: Any) -> CallerWorkspace:
    """The workspace this request acts on when it names none."""
    daemon = daemon_workspace(app_state)
    rbac = getattr(app_state, "rbac", None)
    token = session_token_from_scope(scope)
    if not token:
        binding = _owner_binding(scope, app_state)
        if binding:
            scope.setdefault("state", {})[_OWNER_KEY] = binding
            store = session_workspaces(app_state)
            key = "owner:" + binding
            chosen = store.get(key) or daemon
            store.bind(key, chosen)
            return CallerWorkspace(chosen, bound=True)
        return CallerWorkspace(daemon, bound=False)
    if rbac is None:
        return CallerWorkspace(daemon, bound=False)
    user = rbac.resolve_session(token)
    if not user:
        return CallerWorkspace(daemon, bound=False)  # the gates refuse it
    store = session_workspaces(app_state)
    from superlocalmemory.access.rbac import Permission

    chosen = store.get(token)
    if chosen and rbac.has_permission(user["user_id"], chosen, Permission.READ):
        return CallerWorkspace(chosen, bound=True)
    first = _first_readable(rbac, user["user_id"], daemon)
    if first is None:
        return CallerWorkspace(daemon, bound=False)  # no workspace: refused later
    store.bind(token, first)
    return CallerWorkspace(first, bound=True)


def pinned_workspace(scope: dict) -> CallerWorkspace | None:
    state = scope.get("state")
    return state.get(PIN_KEY) if isinstance(state, dict) else None


def bound_workspace() -> str | None:
    """This request's caller-chosen workspace, if the caller has one.

    Routes that act through the daemon's engine (a save or a recall that names
    no workspace) route to it, so a signed-in person's save lands in the
    workspace they are in, not in the machine owner's.
    """
    from superlocalmemory.server.profile_runtime import current_request_pin

    pin = current_request_pin()
    return pin.profile_id if pin is not None and pin.bound else None


def route_to_bound(engine_profile: str) -> str:
    """The workspace to route an engine call to, or ``""`` for the engine's own."""
    bound = bound_workspace()
    return bound if bound and bound != engine_profile else ""


def switch_http_workspace(profile_id: str) -> bool:
    """Bind only this authenticated HTTP session after checking target READ."""
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.core.caller_session import presented_session

    state = _REQUEST_STATE.get()
    if state is None:
        return False
    token = presented_session()
    rbac = getattr(state, "rbac", None)
    binding = current_owner_workspace_binding()
    if binding and not token:
        if not _OWNER_PRESENTED.get():
            raise ValueError(
                "This HTTP transport cannot identify a persistent connection. "
                "Use Mcp-Session-Id on a stateful transport, retain the workspace "
                "cookie, send a stable X-SLM-Workspace-Binding, or pass profile_id "
                "on each call. The workspace was not switched."
            )
        # Only personal mode produces this binding; recheck policy at switch.
        if rbac is not None and rbac.require_login():
            raise PermissionError("Sign in with a viewer role or higher to switch workspace.")
        engine = getattr(state, "engine", None)
        db = getattr(engine, "_db", None)
        rows = (db.execute("SELECT 1 FROM profiles WHERE profile_id = ?", (profile_id,))
                if db else [])
        if not rows:
            raise ValueError("The target workspace does not exist.")
        session_workspaces(state).bind("owner:" + binding, profile_id)
        return True
    user = rbac.resolve_session(token) if rbac is not None and token else None
    if user is None or not rbac.has_permission(user["user_id"], profile_id, Permission.READ):
        raise PermissionError("A viewer role or higher on the target workspace is required.")
    session_workspaces(state).bind(token, profile_id)
    return True


class CallerWorkspaceMiddleware:
    """Resolve and pin the caller's workspace before any gate runs."""

    def __init__(self, app, *, app_state) -> None:
        self._app = app
        self._app_state = app_state

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        import asyncio

        from superlocalmemory.server.profile_runtime import pin_request_workspace

        try:
            pin = await asyncio.to_thread(resolve_caller_workspace, scope, self._app_state)
        except Exception:  # noqa: BLE001 - unreadable roles: the gates answer 503
            logger.warning("caller workspace could not be resolved", exc_info=True)
            if str(scope.get("path", "")).startswith("/mcp"):
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"error": "Workspace resolution temporarily unavailable",
                     "code": "WORKSPACE_UNAVAILABLE", "retryable": True}, status_code=503,
                )
                await response(scope, receive, send)
                return
            await self._app(scope, receive, send)
            return
        scope.setdefault("state", {})[PIN_KEY] = pin
        state_token = _REQUEST_STATE.set(self._app_state)
        binding = scope.get("state", {}).get(_OWNER_KEY, "")
        owner_token = _OWNER_BINDING.set(binding)
        presented_token = _OWNER_PRESENTED.set(
            scope.get("state", {}).get(_OWNER_PRESENTED_KEY, False)
        )

        async def send_with_binding(message):
            if binding and message.get("type") == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                cookie = f"{_OWNER_COOKIE}={binding}; Path=/mcp; HttpOnly; SameSite=Strict"
                message["headers"].append((b"set-cookie", cookie.encode("latin-1")))
            await send(message)

        try:
            with pin_request_workspace(pin):
                await self._app(scope, receive, send_with_binding)
        finally:
            _OWNER_PRESENTED.reset(presented_token)
            _OWNER_BINDING.reset(owner_token)
            _REQUEST_STATE.reset(state_token)


__all__ = [
    "CallerWorkspace",
    "CallerWorkspaceMiddleware",
    "PIN_KEY",
    "SessionWorkspaces",
    "bound_workspace",
    "pinned_workspace",
    "resolve_caller_workspace",
    "route_to_bound",
    "session_token_from_scope",
    "session_workspaces",
]
