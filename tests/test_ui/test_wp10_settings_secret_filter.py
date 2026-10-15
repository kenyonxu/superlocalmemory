"""The recovery settings filter drops secrets, not every key that mentions one.

PR 251 review, finding 9: the filter matched substrings, so ``max_tokens``,
``token_budget``, ``cache_key`` and the like were never backed up or restored,
and the "Settings" scope was quietly incomplete.
"""

from __future__ import annotations

import pytest

from superlocalmemory.server import backup_bundle

SETTINGS = {
    "max_tokens": 4096,
    "per_memory_max_tokens": 300,
    "token_budget": 5000,
    "cache_key": "recall-v2",
    "key_id": "a1b2",
    "env_key": "OPENAI_API_KEY",
    "require_install_token": True,
    "api_key": "fixture-key",
    "llm": {"api_key": "fixture-nested", "max_tokens": 512, "provider": "openai"},
    "github_client_secret": "fixture-client-secret",
    "refresh_token": "fixture-refresh",
    "access_token": "fixture-access",
    "password": "fixture-password",
    "credentials": "fixture-credentials",
    "recovery_key": "fixture-recovery",
    "token": "fixture-token",
    "oauth": {"credentials": {"user": "fixture-user", "pass": "fixture-pass"}},
}

KEPT = {"max_tokens", "per_memory_max_tokens", "token_budget", "cache_key", "key_id",
        "env_key", "require_install_token", "llm", "oauth"}


def test_only_secret_values_are_dropped():
    safe = backup_bundle._safe_settings(SETTINGS)
    assert set(safe) == KEPT
    assert safe["llm"] == {"max_tokens": 512, "provider": "openai"}
    assert safe["oauth"] == {}
    assert "fixture" not in repr(safe)


@pytest.mark.parametrize("name", sorted(KEPT - {"llm", "oauth"}))
def test_a_changed_destination_clears_secrets_but_not_ordinary_settings(name):
    current = {"api_base": "https://new.example", "api_key": "fixture-live", name: SETTINGS[name]}
    backup_bundle._merge_settings(current, {"api_base": "https://old.example"})
    assert current["api_key"] == ""
    assert current[name] == SETTINGS[name]
