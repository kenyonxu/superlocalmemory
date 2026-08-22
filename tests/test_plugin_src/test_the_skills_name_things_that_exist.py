# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A worked example in a skill file is a contract an assistant will copy.

The recall example named channels the engine does not have (`lexical`,
`structural`), said six where there are seven, and gave a `fact_type` that is
not one of the four. An assistant reading it would look for keys that never
arrive and filter on a value nothing is ever stored under.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from superlocalmemory.retrieval.channel_status import CHANNEL_NAMES
from superlocalmemory.storage.models import FactType

REPO = Path(__file__).resolve().parents[2]
SKILLS = sorted((REPO / "plugin-src" / "skills").glob("*/SKILL.md"))

FACT_TYPES = {f.value for f in FactType}


def _json_blocks(text: str) -> list[dict]:
    out: list[dict] = []
    for block in re.findall(r"```json\n(.*?)```", text, re.S):
        try:
            parsed = json.loads(block)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _walk(node, key: str):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_named_channel_exists(skill: Path) -> None:
    for block in _json_blocks(skill.read_text(encoding="utf-8")):
        for field in ("channel_scores", "channel_weights", "channel_status"):
            for mapping in _walk(block, field):
                if not isinstance(mapping, dict):
                    continue
                unknown = sorted(set(mapping) - set(CHANNEL_NAMES))
                assert not unknown, (
                    f"{skill.parent.name} shows {field} keys the engine never "
                    f"emits: {unknown}"
                )


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_named_fact_type_exists(skill: Path) -> None:
    for block in _json_blocks(skill.read_text(encoding="utf-8")):
        for value in _walk(block, "fact_type"):
            assert value in FACT_TYPES, (
                f"{skill.parent.name} shows fact_type {value!r}; the four are "
                f"{sorted(FACT_TYPES)}"
            )


def test_the_stated_channel_count_is_the_real_one() -> None:
    text = (REPO / "plugin-src" / "skills" / "slm-recall" / "SKILL.md").read_text("utf-8")
    words = {6: "six", 7: "seven", 8: "eight"}
    expected = words[len(CHANNEL_NAMES)]
    stated = re.findall(
        r"\b(six|seven|eight)\b[^.\n]{0,40}channels|"
        r"channels?[^.\n]{0,40}\b(six|seven|eight)\b",
        text,
    )
    stated = [m for pair in stated for m in pair if m]
    assert stated, "the skill no longer states a channel count"
    assert set(stated) == {expected}, (
        f"the skill says {set(stated)} channels; there are {len(CHANNEL_NAMES)}"
    )
