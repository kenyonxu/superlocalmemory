# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Give every hook payload Claude's snake_case keys, whichever host sent it.

Grok CLI runs Claude Code plugins' hooks with camelCase keys (``sessionId``,
``toolName``, ``toolInput``); the SLM hooks read ``session_id``, ``tool_name``
and ``tool_input``. ``handle_hook`` calls :func:`normalize_stdin` once, before
any handler reads stdin. A key already present is never overwritten: Grok's
``hook_event_name`` carries Claude's event name and must stay as sent.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

# Same bound as the outcome hooks' own read (_outcome_common).
_READ_CAP = 200_000

_CAMEL_TO_SNAKE = {
    "sessionId": "session_id",
    "toolName": "tool_name",
    "toolInput": "tool_input",
    "toolResponse": "tool_response",
    "transcriptPath": "transcript_path",
    "permissionMode": "permission_mode",
}


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload`` with snake_case copies of known camelCase keys."""
    out = dict(payload)
    for camel, snake in _CAMEL_TO_SNAKE.items():
        if camel in payload and snake not in payload:
            out[snake] = payload[camel]
    return out


def normalize_stdin() -> None:
    """Rewrite a JSON-object stdin with snake_case keys; leave anything else as read."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        return
    try:
        raw = stream.read(_READ_CAP + 1)
    except Exception:
        return
    text = raw
    if len(raw) <= _READ_CAP:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and any(k in data for k in _CAMEL_TO_SNAKE):
            text = json.dumps(normalize_payload(data))
    sys.stdin = io.StringIO(text)
