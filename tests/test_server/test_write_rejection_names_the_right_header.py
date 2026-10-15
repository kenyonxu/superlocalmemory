# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A refused write names the credential this caller can use (4.1.25 audit L-20).

The refusal used to say "this origin requires an API key (X-SLM-API-Key)" first
to every caller. A caller on this computer has no API key to send; what it needs
is the local install token, in X-Install-Token. A caller on another host does
need the API key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from superlocalmemory.server import write_identity
from superlocalmemory.server.write_identity import (
    require_http_mutation_actor,
    require_write_actor,
)


class _Request:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=host)


def _detail(call) -> str:
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 403
    return str(caught.value.detail)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_a_local_caller_is_told_to_send_the_install_token(host, monkeypatch) -> None:
    monkeypatch.setattr("superlocalmemory.core.security_primitives.verify_install_token",
                        lambda token: False)
    message = _detail(lambda: require_write_actor(
        _Request(host, {"X-Install-Token": "stale"}), None))
    assert "X-Install-Token" in message
    assert message.index("X-Install-Token") < message.index("X-SLM-API-Key")
    assert "this computer" in message and "slm token show" in message


def test_a_remote_caller_is_still_told_to_send_an_api_key() -> None:
    message = _detail(lambda: require_write_actor(_Request("192.0.2.44"), None))
    assert message.startswith("Write rejected: this origin requires an API key (X-SLM-API-Key)")
    assert "SLM_API_KEY" in message


def test_a_local_caller_with_no_credential_is_told_the_install_token(monkeypatch) -> None:
    monkeypatch.setattr(write_identity, "_REQUIRE_CREDENTIALS", True)
    monkeypatch.setattr(write_identity, "_TEST_ISOLATION_ALLOWED", False)
    message = _detail(lambda: require_http_mutation_actor(_Request("127.0.0.1"), None))
    assert message.startswith("Mutation rejected:")
    assert message.index("X-Install-Token") < message.index("X-SLM-API-Key")


def test_a_remote_mutation_refusal_keeps_its_wording() -> None:
    message = _detail(lambda: require_http_mutation_actor(_Request("192.0.2.44"), None))
    assert "Mutation rejected" in message and "X-SLM-API-Key" in message
    assert "Uncredentialed writes are accepted only from loopback" in message
