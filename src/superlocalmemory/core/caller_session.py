# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""The user session a tool call arrived with, on whichever transport carried it.

One person, one identity, whichever door they use (4.1.25 audit OPS-16). Over
REST the daemon reads the session from the ``X-SLM-User-Session`` header or
the dashboard's ``slm_session`` cookie. The MCP tools served by the same daemon
over HTTP used to look only at the ``SLM_USER_SESSION`` environment variable,
which is the daemon's own environment, not the caller's: a valid sign-in that
REST accepted was discarded, and the caller was treated as anonymous.

The HTTP MCP mount now binds the session it received to this context variable
for the length of one request. A bound value, even an empty one, is the whole
answer for that request: the daemon's environment is never a caller's
identity. Off HTTP (the stdio MCP process, the command line) nothing is bound
and the process environment is the caller's own, as before.
"""

from __future__ import annotations

import contextvars
import os
from http.cookies import SimpleCookie
from typing import Any

SESSION_ENV = "SLM_USER_SESSION"
SESSION_HEADER = b"x-slm-user-session"
SESSION_COOKIE = "slm_session"

#: ``None`` = no HTTP request in scope; a string = the session that request carried.
_http_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_http_user_session", default=None,
)


def presented_session() -> str:
    """The session token the current caller presented, or ``""``."""
    bound = _http_session.get()
    if bound is not None:
        return bound
    return os.environ.get(SESSION_ENV, "").strip()


def http_session_bound() -> bool:
    """Whether this call belongs to HTTP (including an empty presented session)."""
    return _http_session.get() is not None


def session_from_scope(scope: dict[str, Any]) -> str:
    """The session an ASGI HTTP request carries: header first, then the cookie."""
    cookie_header = ""
    for name, value in scope.get("headers") or ():
        lowered = name.lower()
        if lowered == SESSION_HEADER:
            token = value.decode("latin-1").strip()
            if token:
                return token
        elif lowered == b"cookie":
            cookie_header = value.decode("latin-1")
    if not cookie_header:
        return ""
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:  # noqa: BLE001 - a malformed cookie carries no session
        return ""
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value.strip() if morsel is not None else ""


class CallerSessionASGI:
    """Bind the HTTP caller's session for the MCP tools this request reaches."""

    __slots__ = ("_app",)

    def __init__(self, inner: Any) -> None:
        self._app = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        token = _http_session.set(session_from_scope(scope))
        try:
            await self._app(scope, receive, send)
        finally:
            _http_session.reset(token)


def bind_session(value: str):
    """Bind ``value`` as the current caller's session (tests, in-process relays).

    Returns the reset token for :func:`unbind_session`.
    """
    return _http_session.set(value)


def unbind_session(token) -> None:
    _http_session.reset(token)


__all__ = [
    "CallerSessionASGI",
    "SESSION_ENV",
    "bind_session",
    "presented_session",
    "http_session_bound",
    "session_from_scope",
    "unbind_session",
]
