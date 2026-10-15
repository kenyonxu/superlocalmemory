# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""The npm package carries every plugin folder the docs point at.

docs/plugins.md tells npm users to install plugins from
``$(npm root -g)/superlocalmemory/<folder>``. 4.2.6 moved the Cursor-format
plugin out of ``plugin/`` into its own ``cursor-plugin/`` with a root
``.cursor-plugin/marketplace.json``, and package.json ``files`` still listed
only the old folders, so the npm package would have shipped no Cursor plugin
(14 Oct 2026).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FILES = set(json.loads((REPO / "package.json").read_text(encoding="utf-8"))["files"])

PLUGIN_FOLDERS = (
    ".claude-plugin/",
    ".cursor-plugin/",
    "plugin/",
    "cursor-plugin/",
    "codex-plugin/",
    "copilot-plugin/",
    "hermes-plugin/",
    "antigravity-plugin/",
    "muse-plugin/",
)


@pytest.mark.parametrize("folder", PLUGIN_FOLDERS)
def test_npm_files_lists_the_plugin_folder(folder):
    assert (REPO / folder).is_dir(), f"{folder} is missing from the repository"
    assert folder in FILES, f"package.json files does not ship {folder}"


def test_the_vscode_agent_plugin_is_installed_from_github_not_npm():
    """VS Code adds it from the repository (.github/plugin/marketplace.json);
    leaving it out keeps the npm package under its 300-file budget."""
    assert "vscode-agent-plugin/" not in FILES
    assert (REPO / ".github" / "plugin" / "marketplace.json").is_file()
