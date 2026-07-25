# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Security invariant tests for loopback-only install-token enforcement.

These tests verify that the Workstream B fix (issue #90) preserves every
security invariant:
  - Install token is ONLY accepted from loopback (127.x.x.x / ::1 / ::ffff:127.x.x.x).
  - IPv4-mapped private IPs (::ffff:192.168.x.x) are NOT accepted as loopback.
  - Non-loopback callers MUST present an API key; install token is not enough.
  - Valid API key IS accepted from non-loopback (required for remote access).
  - Empty client_host is never trusted (SEC-L-02).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _Req:
    def __init__(self, headers: dict[str, str], client_host: str) -> None:
        self.headers = headers
        self.client = SimpleNamespace(host=client_host)
        self.state = SimpleNamespace(authenticated_actor="")


# ---------------------------------------------------------------------------
# SEC invariant: install token from non-loopback → 403
# ---------------------------------------------------------------------------

def test_non_loopback_install_token_rejected(monkeypatch) -> None:
    """Install token from a LAN IP must fail closed (403).

    This test must pass BOTH before and after the fix: the fix adds
    ::ffff:127.x.x.x to loopback, not LAN addresses.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.infra.auth_middleware.verify_api_key",
        lambda key: False,  # no API key configured
    )

    with pytest.raises(HTTPException) as exc_info:
        require_write_actor(
            _Req({"X-Install-Token": "test-install-token"}, client_host="192.168.1.100"),
            descriptor=None,
        )
    assert exc_info.value.status_code == 403, (
        "Install token from LAN IP must yield 403 — not a loopback address."
    )


def test_ipv4_mapped_private_install_token_rejected(monkeypatch) -> None:
    """Install token from ::ffff:192.168.1.1 must fail closed (403).

    IPv4-mapped private IP must NOT be mistakenly treated as loopback.
    This is the key CRIT-2 (ipv4_mapped bypass) guard.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.infra.auth_middleware.verify_api_key",
        lambda key: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        require_write_actor(
            _Req(
                {"X-Install-Token": "test-install-token"},
                client_host="::ffff:192.168.1.1",
            ),
            descriptor=None,
        )
    assert exc_info.value.status_code == 403, (
        "::ffff:192.168.1.1 is IPv4-mapped PRIVATE, not loopback. Must reject."
    )


# ---------------------------------------------------------------------------
# SEC invariant: valid API key accepted from non-loopback
# ---------------------------------------------------------------------------

def test_api_key_accepted_from_non_loopback(monkeypatch) -> None:
    """Valid API key from a LAN IP must succeed.

    Non-loopback callers CAN authenticate with an API key. This is the
    designed network access path: install SLM_API_KEY and use X-SLM-API-Key.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.infra.auth_middleware.verify_api_key",
        lambda key: key == "valid-api-key",
    )

    actor = require_write_actor(
        _Req({"X-SLM-API-Key": "valid-api-key"}, client_host="192.168.1.100"),
        descriptor=None,
        actor_kind="http-api",
    )
    assert actor.startswith("api-key:http-api:"), (
        "Valid API key from non-loopback must return an api-key actor."
    )
    assert "valid-api-key" not in actor, "Actor must not echo the raw key."


# ---------------------------------------------------------------------------
# SEC invariant: empty client host (SEC-L-02) is never trusted
# ---------------------------------------------------------------------------

def test_empty_client_host_install_token_rejected(monkeypatch) -> None:
    """SEC-L-02: empty client_host + install token must fail closed (403).

    A missing peer address (proxy strips it) must never be trusted.
    """
    from superlocalmemory.server.write_identity import require_write_actor

    monkeypatch.setattr(
        "superlocalmemory.core.security_primitives.verify_install_token",
        lambda token: token == "test-install-token",
    )
    monkeypatch.setattr(
        "superlocalmemory.infra.auth_middleware.verify_api_key",
        lambda key: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        require_write_actor(
            _Req({"X-Install-Token": "test-install-token"}, client_host=""),
            descriptor=None,
        )
    assert exc_info.value.status_code == 403


def test_empty_client_host_uncredentialed_rejected(monkeypatch) -> None:
    """SEC-L-02: uncredentialed empty client_host must fail closed (403)."""
    from superlocalmemory.server.write_identity import require_http_mutation_actor

    # No credentials, no loopback → must 403
    with pytest.raises(HTTPException) as exc_info:
        require_http_mutation_actor(
            _Req({}, client_host=""),
            descriptor=None,
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# SEC invariant: authorize_http_mcp_request non-loopback requires API key
# ---------------------------------------------------------------------------

def test_mcp_auth_non_loopback_without_key_is_rejected() -> None:
    """Non-loopback MCP caller without API key must be denied."""
    from superlocalmemory.infra.auth_middleware import authorize_http_mcp_request

    result = authorize_http_mcp_request(
        {},  # no X-SLM-API-Key
        client_host="192.168.1.100",
    )
    assert result is False, "Non-loopback MCP request without API key must be denied."


def test_mcp_auth_ipv4_mapped_loopback_is_accepted() -> None:
    """#90: MCP gate must accept ::ffff:127.0.0.1 as loopback without API key."""
    from superlocalmemory.infra.auth_middleware import authorize_http_mcp_request

    result = authorize_http_mcp_request(
        {},
        client_host="::ffff:127.0.0.1",
    )
    assert result is True, (
        "::ffff:127.0.0.1 is IPv4-mapped loopback — MCP gate must accept it."
    )
