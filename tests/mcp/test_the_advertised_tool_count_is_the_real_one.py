# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""A number in a marketplace listing is a promise, and it goes stale silently.

The plugin description advertises how many tools its profile exposes. That
count sat at 21 while the profile actually carried 32, across several releases,
because nothing compared the sentence to the set. This does the comparison.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from superlocalmemory.mcp.profiles import _PROFILE_ALIASES, _PROFILE_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every file that states the count in prose a user reads before installing.
ADVERTISEMENTS = (
    REPO_ROOT / "plugin-src" / "manifest.json",
    REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json",
    REPO_ROOT / ".claude-plugin" / "marketplace.json",
)

_COUNT_CLAIM = re.compile(r"(\d+)-tool (\w+) profile")


def _descriptions(path: Path) -> list[str]:
    """Every 'description' string anywhere in a JSON document."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("path", ADVERTISEMENTS, ids=lambda p: p.name)
def test_an_advertised_tool_count_matches_the_profile(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not present in this checkout")

    claims = [
        m for text in _descriptions(path) for m in _COUNT_CLAIM.finditer(text)
    ]
    assert claims, (
        f"{path.name} no longer states a tool count; if that was deliberate, "
        f"delete this test rather than leaving it passing vacuously"
    )

    for claim in claims:
        advertised = int(claim.group(1))
        raw = claim.group(2).lower()
        name = _PROFILE_ALIASES.get(raw, raw)
        real = len(_PROFILE_DEFINITIONS[name])
        assert advertised == real, (
            f"{path.name} advertises {advertised} tools in the {name!r} "
            f"profile; it actually exposes {real}"
        )
