# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Uniform error envelope for queue-backed callers.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    RATE_LIMITED = "RATE_LIMITED"
    QUEUE_FULL = "QUEUE_FULL"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"
    DAEMON_DOWN = "DAEMON_DOWN"
    INTERNAL = "INTERNAL"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUEUE_FULL: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.DEAD_LETTER: 504,
    ErrorCode.CANCELLED: 499,
    ErrorCode.DAEMON_DOWN: 502,
    ErrorCode.INTERNAL: 500,
}


def http_status_for(code: ErrorCode) -> int:
    return _HTTP_STATUS[code]


def make_error_envelope(
    code: ErrorCode,
    message: str,
    *,
    request_id: str | None = None,
    retry_after_ms: int | None = None,
    **extras: Any,
) -> dict[str, Any]:
    """Build a JSON-serialisable error envelope."""
    env: dict[str, Any] = {
        "ok": False,
        "error_code": code.value,
        "error": message,
        "request_id": request_id,
        "retry_after_ms": retry_after_ms,
    }
    for k, v in extras.items():
        if k not in env:
            env[k] = v
    return env
