# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Resource-safe Streamable-HTTP integration for mcp==2.0.0.

mcp 2.0.0 deleted ``mcp.server.fastmcp.FastMCP``. Replacement is
``mcp.server.mcpserver.MCPServer`` with the same ``.tool()`` decorator and
``run(transport="stdio")``.

Fully-stateless is the default (see ``remote_mode.mcp_stateless`` and
``unified_daemon._configure_mcp_transport_settings``). Under
``stateless_http=True``:

* ``session_idle_timeout`` is illegal (SDK raises RuntimeError) and unused —
  there are no transport sessions to reap.
* the EventStore (SSE Last-Event-ID resumability) is not used.
* therefore the old ``SLMFastMCP.streamable_http_app()`` override that
  pre-created a ``StreamableHTTPSessionManager`` is gone — kwargs go
  straight to ``MCPServer.streamable_http_app(...)``.

Application-level ``session_init`` / ``close_session`` are orthogonal
(session_id is a str param persisted in the memories table) and unchanged.
"""

from __future__ import annotations

import json
import logging

from mcp.server.mcpserver import MCPServer
from sse_starlette.sse import EventSourceResponse
from starlette.types import Receive, Scope, Send

from superlocalmemory import __version__

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE resource guard (still useful if a host opts into non-json SSE responses)
# ---------------------------------------------------------------------------


class ClosingEventSourceResponse(EventSourceResponse):
    """EventSourceResponse that closes the async iterator it consumes."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                await close()


def install_streamable_http_resource_guard() -> None:
    """Install the response owner used by MCP's Streamable-HTTP transport."""
    from mcp.server import streamable_http

    streamable_http.EventSourceResponse = ClosingEventSourceResponse


# ---------------------------------------------------------------------------
# Compact MCP tool-result JSON (Q9, 2026-10-06)
# ---------------------------------------------------------------------------


def install_compact_mcp_json() -> None:
    """Strip the pretty-print whitespace mcp==2.0.0 adds to every tool result.

    ``mcp.server.mcpserver.utilities.func_metadata._convert_to_content`` turns
    a tool's dict/list return value into wire text with
    ``pydantic_core.to_json(result, indent=2)`` — true for EVERY tool in this
    server, ``session_init`` and ``recall`` included. An agent pays per byte,
    not per readable line: indentation alone roughly doubled a large
    response (Q9: a 3,816-member community's thematic_context made
    ``session_init`` 104KB).

    Wraps the vendor function so the same JSON goes out compact instead of
    pretty — same data, same ids, same order, same scores, just no
    whitespace a machine reader never asked for. Non-JSON content (images,
    audio, a tool that already returned a plain string) passes through
    untouched. Idempotent (a second ``SLMFastMCP()`` in the same process does
    not re-wrap). Fails open: if the vendor's private internals move, the
    original pretty-printed behaviour continues rather than crashing the
    server — this only ever changes formatting, never data, so failing open
    costs bytes, not correctness.
    """
    try:
        from mcp.server.mcpserver.utilities import func_metadata as _fm
    except Exception as exc:  # pragma: no cover - defensive, vendor moved
        logger.debug("compact MCP json not installed (import failed): %s", exc)
        return
    original = getattr(_fm, "_convert_to_content", None)
    if not callable(original):  # pragma: no cover - defensive, vendor moved
        logger.debug("compact MCP json not installed: _convert_to_content not found")
        return
    if getattr(original, "_slm_compact_wrapped", False):
        return  # already installed by an earlier SLMFastMCP() in this process

    def _compact_convert_to_content(result):
        blocks = original(result)
        for block in blocks:
            text = getattr(block, "text", None)
            if not isinstance(text, str) or not text:
                continue
            if text.lstrip()[:1] not in ("{", "["):
                continue  # not JSON (or already compact) — leave it alone
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            try:
                block.text = json.dumps(parsed, separators=(",", ":"), default=str)
            except (TypeError, ValueError):
                continue
        return blocks

    _compact_convert_to_content._slm_compact_wrapped = True
    _fm._convert_to_content = _compact_convert_to_content


# ---------------------------------------------------------------------------
# SLMFastMCP — thin MCPServer wrapper (name kept for import stability)
# ---------------------------------------------------------------------------


class SLMFastMCP(MCPServer):
    """MCPServer with SLM release identity.

    Named ``SLMFastMCP`` for backward-compatible imports. Behaviour is
    fully-stateless by default at the *call site* via
    ``streamable_http_app(stateless_http=True, json_response=True, ...)`` —
    this class does not override that method.

    ``version`` is passed to ``MCPServer.__init__`` directly (no private
    ``_mcp_server.version`` poke — that attribute is gone in mcp 2.0.0).
    """

    def __init__(self, *args, product_version: str = __version__, **kwargs) -> None:
        # MCPServer takes version= as a first-class kwarg.
        kwargs.setdefault("version", product_version)
        super().__init__(*args, **kwargs)
        # Optional SSE resource guard for hosts that still request event-stream.
        install_streamable_http_resource_guard()
        # Q9: every tool result goes out compact, not pretty-printed.
        install_compact_mcp_json()
        # UX-13: a tool's {"success": false} answer is reported as isError.
        from superlocalmemory.mcp.tool_gate import install_tool_error_results

        install_tool_error_results()
        self._slm_allowed = None

    def add_tool(self, fn, *args, **kwargs):
        """Register ``fn`` behind the default READ gate (mcp/tool_gate, OPS-17).

        A tool with its own ``@admits`` keeps it; only the public allow-list
        is registered ungated. Every registration path goes through here.
        """
        from superlocalmemory.mcp.tool_gate import default_read_gate

        return super().add_tool(default_read_gate(fn), *args, **kwargs)

    async def read_resource(self, uri, context=None):
        """A refused resource read says why (the SDK hides every other error)."""
        from mcp.server.mcpserver.exceptions import ResourceError
        from mcp.shared.exceptions import MCPError
        from mcp_types import INVALID_REQUEST

        try:
            return await super().read_resource(uri, context)
        except ResourceError as exc:
            cause = exc.__cause__
            while cause is not None and not isinstance(cause, PermissionError):
                cause = cause.__cause__ or cause.__context__
            if cause is not None:
                raise MCPError(code=INVALID_REQUEST, message=str(cause),
                               data={"uri": str(uri), "code": "NOT_AUTHORIZED"}) from exc
            raise

    def resource(self, *args, **kwargs):
        """Register a resource behind the READ gate (``slm://recent`` and the rest)."""
        from superlocalmemory.mcp.tool_gate import gate_resource

        register = super().resource(*args, **kwargs)

        def decorator(fn):
            register(gate_resource(fn))
            return fn

        return decorator

    def configure_tool_surface(self, allowed: frozenset[str]) -> None:
        """Freeze host selection while evaluating feature availability per request."""
        self._slm_allowed = allowed

    def _tool_enabled(self, name: str) -> bool:
        if self._slm_allowed is not None and name not in self._slm_allowed:
            return False
        from superlocalmemory.mcp.profiles import _MEDIA_TOOLS

        if name in _MEDIA_TOOLS:
            from superlocalmemory.runtimes import features

            return bool(features.media_enabled())
        return True

    async def list_tools(self):
        return [tool for tool in await super().list_tools() if self._tool_enabled(tool.name)]

    async def call_tool(self, name, arguments, context=None):
        # Host selection is an invocation boundary. Feature availability only
        # controls discovery: media handlers own actionable opt-in refusals.
        if self._slm_allowed is not None and name not in self._slm_allowed:
            raise ValueError("This tool is not enabled for this connection")
        return await super().call_tool(name, arguments, context)

    async def run_stdio_async(self) -> None:
        from superlocalmemory.mcp.local_lifecycle import run_stdio

        await run_stdio(self, self._run_local_transport)

    async def _run_local_transport(self) -> None:
        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self._lowlevel_server.run(
                read_stream, write_stream,
                self._lowlevel_server.create_initialization_options(
                    notification_options=NotificationOptions(tools_changed=True)),
            )
