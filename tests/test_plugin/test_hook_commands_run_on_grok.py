# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Grok CLI runs the Claude Code plugin, and every hook command must start.

Grok CLI (1.0.50) loads this plugin's hooks.json as is. A command made of one
quoted path and nothing else is executed as a file name with the quotes kept,
so the SessionStart venv hook failed there with "command not found"
(14 Oct 2026). Any command with an operator or argument goes through a shell
and runs. Claude Code runs both forms the same way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_plugin._plugin_sandbox import BUILT_PLUGIN, REPO

HOOK_FILES = (
    REPO / "plugin-src" / "hooks" / "hooks.json",
    BUILT_PLUGIN / "hooks" / "hooks.json",
)
LONE_QUOTED_PATH = re.compile(r'^\s*"[^"]+"\s*$')


def _commands(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        hook["command"]
        for groups in data["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    ]


@pytest.mark.parametrize("path", HOOK_FILES, ids=lambda p: p.parent.parent.name)
def test_no_hook_command_is_a_lone_quoted_path(path):
    lone = [c for c in _commands(path) if LONE_QUOTED_PATH.match(c)]
    assert lone == [], f"Grok CLI cannot start these hooks: {lone}"
