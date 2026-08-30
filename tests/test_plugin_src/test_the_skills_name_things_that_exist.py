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
# Every tree an agent could actually be reading, not just the source. A
# generated copy can rot on its own — that is what the build scripts are for,
# and what a check that only looks at the source cannot see.
SKILLS = sorted(
    list((REPO / "plugin-src" / "skills").glob("*/SKILL.md"))
    + list((REPO / "plugin" / "skills").glob("*/SKILL.md"))
    + list((REPO / "codex-plugin" / "skills").glob("*/SKILL.md"))
    + list((REPO / "copilot-plugin" / ".github" / "prompts").glob("*.prompt.md"))
)

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


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_every_named_channel_exists(skill: Path) -> None:
    for block in _json_blocks(skill.read_text(encoding="utf-8")):
        for field in ("channel_scores", "channel_weights", "channel_status"):
            for mapping in _walk(block, field):
                if not isinstance(mapping, dict):
                    continue
                unknown = sorted(set(mapping) - set(CHANNEL_NAMES))
                assert not unknown, (
                    f"{skill} shows {field} keys the engine never "
                    f"emits: {unknown}"
                )


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_every_named_fact_type_exists(skill: Path) -> None:
    for block in _json_blocks(skill.read_text(encoding="utf-8")):
        for value in _walk(block, "fact_type"):
            assert value in FACT_TYPES, (
                f"{skill} shows fact_type {value!r}; the four are "
                f"{sorted(FACT_TYPES)}"
            )


def test_the_channels_the_skill_names_are_the_channels_that_exist() -> None:
    """The skill must name every channel, and must not invent one.

    This used to demand a spelled-out count -- "seven channels" -- and fail if
    the skill did not carry one. That made a real improvement unshippable: the
    skill stopped claiming seven because counting ``entity_graph`` among the
    searchers is wrong. It returns no candidates of its own; it re-scores what
    the others found, which is why it reports ``no_candidates`` when they come
    back empty. A single number cannot say that, so the skill now describes the
    three roles instead, and the test that insisted on the number was insisting
    on the imprecision.

    What actually matters is the set, not its cardinality: every channel a caller
    will see in ``channel_status`` is described. A count, if one is ever stated
    again, is checked below.

    There is deliberately no "the skill invents no channel" assertion here. Any
    regex loose enough to find one in prose also matches the status values and
    tool names around it -- ``ok``, ``count``, ``recall`` -- so it would fail on
    correct documentation. The names shown in the ``channel_scores`` and
    ``channel_weights`` examples are already checked against ``CHANNEL_NAMES``
    further up this file, where they appear as structured keys rather than prose.
    """
    text = (REPO / "plugin-src" / "skills" / "slm-recall" / "SKILL.md").read_text("utf-8")

    missing = [name for name in CHANNEL_NAMES if f"`{name}`" not in text]
    assert not missing, (
        f"the skill does not name {missing}; a caller reading channel_status "
        f"will see them and find nothing about them here"
    )

    # A count is optional. If one is stated, it has to be right.
    words = {6: "six", 7: "seven", 8: "eight"}
    stated = [
        m
        for pair in re.findall(
            r"\b(six|seven|eight)\b[^.\n]{0,40}channels|"
            r"channels?[^.\n]{0,40}\b(six|seven|eight)\b",
            text,
        )
        for m in pair
        if m
    ]
    if stated:
        assert set(stated) == {words[len(CHANNEL_NAMES)]}, (
            f"the skill says {set(stated)} channels; there are "
            f"{len(CHANNEL_NAMES)}: {sorted(CHANNEL_NAMES)}"
        )
