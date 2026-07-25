# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Regression tests for GitHub issue #90.

Root cause: When SLM_DAEMON_HOST=0.0.0.0 on a dual-stack host the OS creates
an IPv6 socket that reports IPv4 clients as ::ffff:127.0.0.1 (IPv4-mapped
IPv6 loopback). The three literal sets ("127.0.0.1", "::1", "localhost") did
not include this form, so install-token and uncredentialed loopback auth
failed with 403 in container deployments.

These tests FAIL before the fix (403 / wrong result) and PASS after.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _Req:
    """Minimal FastAPI Request stub for unit-testing write_identity functions."""

    def __init__(self, headers: dict[str, str], client_host: str = "127.0.0.1") -> None:
        self.headers = headers
        self.client = SimpleNamespace(host=client_host)
        # Satisfy require_http_mutation_actor's _header() helper
        self.state = SimpleNamespace(authenticated_actor="")


# ---------------------------------------------------------------------------
# Bug site 1: write_identity.py:92 — require_write_actor install-token path
# ---------------------------------------------------------------------------

def test_install_token_from_ipv4_mapped_loopback_is_accepted(monkeypatch) -> None:
    """#90 primary: ::ffff:127.0.0.1 + valid install token must succeed (not 403).

    Before fix: client_host in ("127.0.0.1","::1","localhost") → False → 403.
    After fix:  is_loopback("::ffff:127.0.0.1") → True → local actor returned.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )

    actor = require_write_actor(
        _Req({"X-Install-Token": "test-install-token"}, client_host="::ffff:127.0.0.1"),
        descriptor=None,
        actor_kind="dashboard",
    )
    assert actor == "trusted:dashboard", (
        f"Expected trusted:dashboard, got {actor!r}. "
        "#90 fix: ::ffff:127.0.0.1 must be accepted as loopback."
    )


def test_install_token_from_standard_ipv4_loopback_still_works(monkeypatch) -> None:
    """Baseline: 127.0.0.1 install-token path must still work after the fix."""
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )

    actor = require_write_actor(
        _Req({"X-Install-Token": "test-install-token"}, client_host="127.0.0.1"),
        descriptor=None,
        actor_kind="dashboard",
    )
    assert actor == "trusted:dashboard"


def test_install_token_from_ipv6_loopback_still_works(monkeypatch) -> None:
    """Baseline: ::1 install-token path must still work after the fix."""
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )

    actor = require_write_actor(
        _Req({"X-Install-Token": "test-install-token"}, client_host="::1"),
        descriptor=None,
        actor_kind="dashboard",
    )
    assert actor == "trusted:dashboard"


# ---------------------------------------------------------------------------
# Bug site 2: write_identity.py:137 — require_http_mutation_actor uncredentialed
# ---------------------------------------------------------------------------

def test_uncredentialed_ipv4_mapped_loopback_is_trusted(monkeypatch) -> None:
    """#90 secondary: uncredentialed ::ffff:127.0.0.1 must be treated as loopback.

    Before fix: loopback = "::ffff:127.0.0.1" in ("127.0.0.1","::1","localhost") → False
                → HTTPException(403).
    After fix:  is_loopback("::ffff:127.0.0.1") → True → trusted-actor returned.
    """
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )

    actor = require_http_mutation_actor(
        _Req({}, client_host="::ffff:127.0.0.1"),
        descriptor=None,
        actor_kind="http-route",
    )
    assert actor == "trusted:http-route", (
        f"Expected trusted:http-route, got {actor!r}. "
        "#90 fix: uncredentialed ::ffff:127.0.0.1 must be a trusted loopback actor."
    )


# ---------------------------------------------------------------------------
# Bug site 3: auth_middleware.py:140 — authorize_http_mcp_request
# ---------------------------------------------------------------------------

def test_authorize_http_mcp_request_accepts_ipv4_mapped_loopback() -> None:
    """#90 tertiary: MCP transport gate must accept ::ffff:127.0.0.1 as loopback."""
    from superlocalmemory.infra.auth_middleware import authorize_http_mcp_request

    result = authorize_http_mcp_request(
        {},  # no headers — no api key
        client_host="::ffff:127.0.0.1",
    )
    assert result is True, (
        "authorize_http_mcp_request must accept ::ffff:127.0.0.1 as loopback. "
        "#90 fix required."
    )
