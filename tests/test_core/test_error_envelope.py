# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for core.error_envelope."""

from __future__ import annotations

import pytest


def _import_module():
    from superlocalmemory.core import error_envelope as ee
    return ee


def test_error_codes_enumerated() -> None:
    ee = _import_module()
    names = {c.name for c in ee.ErrorCode}
    assert {
        "RATE_LIMITED", "QUEUE_FULL", "TIMEOUT", "CANCELLED",
        "DEAD_LETTER", "DAEMON_DOWN", "INTERNAL",
    } <= names


def test_envelope_minimal_shape() -> None:
    ee = _import_module()
    env = ee.make_error_envelope(ee.ErrorCode.TIMEOUT, "recall took too long")
    assert env["ok"] is False
    assert env["error_code"] == "TIMEOUT"
    assert env["error"] == "recall took too long"
    assert env["request_id"] is None
    assert env["retry_after_ms"] is None


def test_envelope_with_optional_fields() -> None:
    ee = _import_module()
    env = ee.make_error_envelope(
        ee.ErrorCode.RATE_LIMITED,
        "per-agent bucket drained",
        request_id="r-abc",
        retry_after_ms=2100,
        layer="per-agent",
    )
    assert env["request_id"] == "r-abc"
    assert env["retry_after_ms"] == 2100
    assert env["layer"] == "per-agent"


def test_http_status_mapping() -> None:
    ee = _import_module()
    assert ee.http_status_for(ee.ErrorCode.RATE_LIMITED) == 429
    assert ee.http_status_for(ee.ErrorCode.QUEUE_FULL) == 503
    assert ee.http_status_for(ee.ErrorCode.TIMEOUT) == 504
    assert ee.http_status_for(ee.ErrorCode.DEAD_LETTER) == 504
    assert ee.http_status_for(ee.ErrorCode.CANCELLED) == 499
    assert ee.http_status_for(ee.ErrorCode.DAEMON_DOWN) == 502
    assert ee.http_status_for(ee.ErrorCode.INTERNAL) == 500


def test_envelope_serializable_to_json() -> None:
    import json
    ee = _import_module()
    env = ee.make_error_envelope(ee.ErrorCode.QUEUE_FULL, "depth 50 exceeded")
    # Must round-trip via JSON (MCP wire format)
    blob = json.dumps(env)
    back = json.loads(blob)
    assert back == env
