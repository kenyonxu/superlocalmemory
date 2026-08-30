# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for mcp/session_binding.py — the four-step session-id ladder.

Regression coverage for the 2026-08-24 fix: ``CLAUDE_CODE_SESSION_ID`` is the
variable Claude Code's MCP subprocess environment actually carries, but the
env-var list only checked ``SLM_SESSION_ID`` / ``CLAUDE_SESSION_ID``. Every
recall from Claude Code fell through to the synthetic ``mcp:<agent_id>``
fallback that step 4 deliberately excludes from ever matching a pending
outcome, so the engagement-learning loop (bandit arms, source quality)
settled at the neutral 0.5 label regardless of real usage. Verified on the
live store: 6,013 of 6,039 settled outcomes at exactly reward=0.5.
"""

from __future__ import annotations

from superlocalmemory.mcp import session_binding


def test_explicit_argument_always_wins(monkeypatch) -> None:
    monkeypatch.setenv("SLM_SESSION_ID", "env-value")
    result = session_binding.resolve_session_id("explicit-value", agent_id="claude")
    assert result == "explicit-value"


def test_explicit_argument_ignores_surrounding_whitespace(monkeypatch) -> None:
    monkeypatch.delenv("SLM_SESSION_ID", raising=False)
    result = session_binding.resolve_session_id("  padded-value  ", agent_id="claude")
    assert result == "padded-value"


def test_finds_claude_code_session_id_env_var(monkeypatch) -> None:
    """Regression test for the 2026-08-24 fix — the actual bug."""
    monkeypatch.delenv("SLM_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "real-claude-code-session")
    # Force past step 3 so this isolates the env-var check (step 2).
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.lookup_by_parent",
        lambda within_seconds=60: "",
    )
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.most_recent_active",
        lambda agent_type="claude", within_seconds=60: "",
    )
    result = session_binding.resolve_session_id("", agent_id="claude_code")
    assert result == "real-claude-code-session"


def test_slm_session_id_takes_precedence_over_claude_code_session_id(
    monkeypatch,
) -> None:
    """SLM's own override wins when a host's variable is set to something
    unhelpful — this is the documented purpose of checking SLM_SESSION_ID
    first in SESSION_ENV_VARS."""
    monkeypatch.setenv("SLM_SESSION_ID", "slm-override")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-code-value")
    result = session_binding.resolve_session_id("", agent_id="claude_code")
    assert result == "slm-override"


def test_falls_back_to_synthetic_id_when_nothing_resolves(monkeypatch) -> None:
    monkeypatch.delenv("SLM_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.lookup_by_parent",
        lambda within_seconds=60: "",
    )
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.most_recent_active",
        lambda agent_type="claude", within_seconds=60: "",
    )
    result = session_binding.resolve_session_id(
        "", agent_id="some_agent", allow_agent_fallback=True,
    )
    assert result == "mcp:some_agent"


def test_returns_empty_when_fallback_disallowed(monkeypatch) -> None:
    monkeypatch.delenv("SLM_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.lookup_by_parent",
        lambda within_seconds=60: "",
    )
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.most_recent_active",
        lambda agent_type="claude", within_seconds=60: "",
    )
    result = session_binding.resolve_session_id(
        "", agent_id="some_agent", allow_agent_fallback=False,
    )
    assert result == ""


def test_session_registry_takes_priority_over_synthetic_fallback(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SLM_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(
        "superlocalmemory.hooks.session_registry.lookup_by_parent",
        lambda within_seconds=60: "registry-match",
    )
    result = session_binding.resolve_session_id("", agent_id="claude_code")
    assert result == "registry-match"
