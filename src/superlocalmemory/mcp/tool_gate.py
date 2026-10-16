# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Every MCP tool and resource asks who is calling; a refusal is a tool error.

The MCP twin of ``server/mutation_gate``. Tools that change state were gated
one by one with ``@admits``; 49 read tools and every resource were not, so in
company mode an anonymous HTTP MCP caller read another workspace's behavioural
assertions, skill history, brain evidence, status counts, cached originals and
peer identities (4.1.25 audit OPS-08, OPS-17), and ``slm://recent`` returned
recent memories to anyone.

Deny by default: a tool registered without its own ``@admits`` is admitted as
a READ (``OperationKind.RECALL``) on the workspace its ``profile_id`` names, or
the active one. Only :data:`PUBLIC_MCP_TOOLS` skip that, each with its reason.
In personal mode READ is always admitted, so nothing changes there.

UX-13: a tool that answers ``{"success": False, ...}`` used to reach the client
as a successful call (``isError: false``). :func:`install_tool_error_results`
marks such results as tool errors, keeping the same JSON (with ``code`` and
``retryable``) as the text and as structured content.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Tools anyone may call, even where every person must sign in, with why.
PUBLIC_MCP_TOOLS: dict[str, str] = {
    "get_version": "package version, Python version and platform; no memory, no settings",
}


def _refused(kind, profile_id: str) -> dict | None:
    from superlocalmemory.core.actor_context import Transport
    from superlocalmemory.core.admission import (
        AdmissionDenied,
        admit,
        caller_actor,
        refusal_result,
    )

    actor, mode = caller_actor(Transport.MCP, profile_id)
    try:
        admit(kind, actor, mode=mode)
    except AdmissionDenied as exc:
        return refusal_result(exc.decision.reason, kind)
    return None


def default_read_gate(fn: Callable) -> Callable:
    """``fn`` admitted as a READ, unless it is public or already gated."""
    from superlocalmemory.core.admission import _GATED_MCP_TOOLS
    from superlocalmemory.core.operation_request import OperationKind

    name = fn.__name__
    if name in PUBLIC_MCP_TOOLS or getattr(fn, "_slm_admission_gated", False):
        return fn
    _GATED_MCP_TOOLS.add(name)
    kind = OperationKind.RECALL

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def gated(*args, **kwargs):
            refusal = _refused(kind, str(kwargs.get("profile_id", "") or ""))
            return refusal if refusal is not None else await fn(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def gated(*args, **kwargs):
            refusal = _refused(kind, str(kwargs.get("profile_id", "") or ""))
            return refusal if refusal is not None else fn(*args, **kwargs)
    gated._slm_admission_gated = True
    return gated


def gate_resource(fn: Callable) -> Callable:
    """A resource read is a READ on the active workspace; a refusal is an error."""
    from superlocalmemory.core.operation_request import OperationKind

    def _check() -> None:
        refusal = _refused(OperationKind.RECALL, "")
        if refusal is not None:
            raise PermissionError(f"not_authorized: {refusal['message']}")

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def gated(*args, **kwargs):
            _check()
            return await fn(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def gated(*args, **kwargs):
            _check()
            return fn(*args, **kwargs)
    return gated


def is_tool_failure(result: Any) -> bool:
    """A tool's own "this did not work" answer (validation, refusal, failure)."""
    if not isinstance(result, dict):
        return False
    if result.get("success") is False:
        return True
    return result.get("ok") is False and ("error" in result or "code" in result)


def install_tool_error_results() -> None:
    """Report a tool's failure answer as an MCP tool error (``isError: true``)."""
    try:
        from mcp.server.mcpserver.utilities import func_metadata as _fm
        from mcp_types import CallToolResult
    except Exception as exc:  # pragma: no cover - vendor moved
        logger.debug("tool error results not installed: %s", exc)
        return
    original = _fm.FuncMetadata.convert_result
    if getattr(original, "_slm_tool_errors", False):
        return

    def convert_result(self, result):
        if is_tool_failure(result):
            import json

            plain = json.loads(json.dumps(result, default=str))
            return CallToolResult(
                content=_fm._convert_to_content(plain),  # compact JSON wrapper applies
                structured_content=plain,
                is_error=True,
            )
        return original(self, result)

    convert_result._slm_tool_errors = True
    _fm.FuncMetadata.convert_result = convert_result


__all__ = [
    "PUBLIC_MCP_TOOLS",
    "default_read_gate",
    "gate_resource",
    "install_tool_error_results",
    "is_tool_failure",
]
