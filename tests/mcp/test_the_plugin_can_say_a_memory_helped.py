# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Ranking by usefulness needs someone to say what was useful.

Retrieval promotes memories that have demonstrably helped. The evidence for
"helped" arrives over MCP, from the assistant that used the memory, through
``report_outcome`` or ``report_feedback``. If the profile the plugin actually
launches with does not expose either tool, the ranker has no input and the
feature is inert for everyone installing the plugin.

These tests read the shipped launch configuration rather than hard-coding a
profile name, so changing which profile the plugin ships also moves the
assertion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from superlocalmemory.mcp.profiles import (
    _PROFILE_ALIASES,
    _PROFILE_DEFINITIONS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_MCP_JSON = REPO_ROOT / "plugin-src" / ".mcp.json"

FEEDBACK_TOOLS = ("report_outcome", "report_feedback")


def _shipped_profile_name() -> str:
    """The profile the plugin launches its MCP server with."""
    data = json.loads(PLUGIN_MCP_JSON.read_text(encoding="utf-8"))
    env = data["mcpServers"]["superlocalmemory"]["env"]
    raw = str(env["SLM_MCP_PROFILE"]).strip().lower()
    return _PROFILE_ALIASES.get(raw, raw)


@pytest.mark.skipif(
    not PLUGIN_MCP_JSON.exists(),
    reason="plugin-src/.mcp.json is not present in this checkout",
)
@pytest.mark.parametrize("tool", FEEDBACK_TOOLS)
def test_the_profile_the_plugin_ships_can_report_usefulness(tool: str) -> None:
    name = _shipped_profile_name()
    tools = _PROFILE_DEFINITIONS[name]
    assert tool in tools, (
        f"the plugin launches with profile {name!r} and it does not expose "
        f"{tool!r}, so nothing installed from the plugin can tell the ranker "
        f"that a memory helped"
    )


@pytest.mark.parametrize("tool", FEEDBACK_TOOLS)
def test_every_profile_above_core_can_report_usefulness(tool: str) -> None:
    """core is the deliberate floor; anything richer carries the loop."""
    for name in ("code", "full", "power"):
        assert tool in _PROFILE_DEFINITIONS[name], (
            f"{name!r} is a working profile and cannot report usefulness"
        )


def test_a_richer_profile_is_a_superset_of_a_simpler_one() -> None:
    core = _PROFILE_DEFINITIONS["core"]
    code = _PROFILE_DEFINITIONS["code"]
    full = _PROFILE_DEFINITIONS["full"]
    power = _PROFILE_DEFINITIONS["power"]
    assert core <= code, sorted(core - code)
    assert core <= full, sorted(core - full)
    assert full <= power, sorted(full - power)
