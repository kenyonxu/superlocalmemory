# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""One absolute clock from HTTP ingress through middleware and serialization."""
import asyncio
from contextvars import ContextVar

_DEADLINE = ContextVar("slm_http_recall_deadline", default=None)
_RECALL_PATHS = frozenset({"/recall", "/api/v3/recall/trace", "/api/v3/views/run"})


def is_recall(scope):
    return scope.get("type") == "http" and scope.get("path", "").rstrip("/") in _RECALL_PATHS


def explicit_scope(scope):
    """The request is a question someone waits on (``recall_core.is_explicit``).

    The prompt hook says so in the query string (``answer_check=skip``), a
    relayed call with a ``budget_s``; they keep the short clock. ``depth=0``
    and ``answer_check=context`` (session_init) are waited on too (4.2.3).
    """
    if scope.get("path", "").rstrip("/") != "/recall":
        return True
    from urllib.parse import parse_qs

    params = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    first = {k: (v[0] if v else "") for k, v in params.items()}
    if first.get("answer_check", "").strip().lower() == "skip":
        return False
    if first.get("budget_s", "").strip():
        return False
    return True


def remaining_deadline(budget):
    deadline = asyncio.get_running_loop().time() + budget
    ingress = _DEADLINE.get()
    return min(deadline, ingress - 0.01) if ingress is not None else deadline


def fallback_deadline(public_deadline, floor_s):
    """When the keyword fallback must answer by: the public deadline, or later
    when that came sooner than the fallback's floor (a shortened budget), yet
    never past the HTTP request's own clock."""
    floor = asyncio.get_running_loop().time() + floor_s
    ingress = _DEADLINE.get()
    if ingress is not None:
        floor = min(floor, ingress - 0.01)
    return max(public_deadline, floor)


async def serve(app, state, scope, receive, send):
    from starlette.responses import JSONResponse

    from superlocalmemory.server.recall_core import (
        EXPLICIT_CEILING_S,
        RECALL_BUDGET_FLOOR_S,
        recall_budget_s,
    )

    # An explicit question may queue for a slot (recall_core.EXPLICIT_QUEUE_S);
    # a prompt-blocking recall keeps its own three-second budget inside this.
    # A budget shortened below the floor gives up on full recall sooner; the
    # keyword fallback that answers then still needs the floor (recall_core).
    ceiling = max(recall_budget_s(), RECALL_BUDGET_FLOOR_S)
    if explicit_scope(scope):
        ceiling = max(ceiling, EXPLICIT_CEILING_S + 0.5)
    deadline = remaining_deadline(ceiling)
    token = _DEADLINE.set(deadline)
    messages = []

    async def buffered_send(message):
        messages.append(message)

    try:
        await asyncio.wait_for(app(scope, receive, buffered_send),
                               max(0, deadline - asyncio.get_running_loop().time()))
    except asyncio.TimeoutError:
        # Admission never completed: do not reveal a profile or touch its store.
        response = JSONResponse({
            "ok": False, "results": [], "count": 0, "result_count": 0,
            "code": "RECALL_BUSY", "retryable": True, "retry_after_s": 5,
            "detail": {"code": "RECALL_BUSY",
                       "message": "SuperLocalMemory is busy; ask again in a few seconds."},
            "degraded_reason": "recall_budget_exceeded", "answerability": "unjudged",
            "answer_check_status": "skipped", "answer_check_detail": "budget",
            "answer_check_note": ("Recall timed out before full results were available; "
                                  "it was not checked."),
        }, status_code=503, headers={"Retry-After": "5"})
        await response(scope, receive, send)
    else:
        for message in messages:
            await send(message)
    finally:
        _DEADLINE.reset(token)
