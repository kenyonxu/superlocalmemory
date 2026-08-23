# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""A plugin an editor cannot list is a plugin nobody has.

WHAT WAS WRONG

``codex-plugin/`` shipped twelve skills, hooks, launcher scripts and a
``config.toml`` — and no manifest naming any of it. Codex reads
``.codex-plugin/plugin.json`` to register a plugin, so with the file absent there
was nothing to register: the plugin never appeared under Plugins and could not be
enabled from the interface. Everything was delivered and none of it was visible.

Separately, the Claude Code marketplace entry carried no ``version``. A client
comparing what it has against what a marketplace offers has nothing to compare,
so an installed plugin never looks out of date however many releases pass. That
is what "no upgrade in the plugins" meant.

Both were invisible failures — nothing errored, nothing warned, the files were
all present and the versions all correct. Only the absence of a manifest key
stood between a working plugin and one nobody could find. So these tests assert
the shape an editor actually reads, not that the directory exists.
"""

from __future__ import annotations

import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

#: (label, manifest path, the key path holding the version)
MANIFESTS = (
    ("Claude Code marketplace", ".claude-plugin/marketplace.json", ("plugins", 0, "version")),
    ("Claude Code plugin", "plugin/.claude-plugin/plugin.json", ("version",)),
    ("Codex plugin", "codex-plugin/.codex-plugin/plugin.json", ("version",)),
    ("Antigravity plugin", "antigravity-plugin/plugin.json", ("version",)),
)


def _load(rel: str):
    path = REPO / rel
    assert path.is_file(), f"{rel} is missing — the editor has nothing to register"
    return json.loads(path.read_text(encoding="utf-8"))


def _dig(data, keys):
    for key in keys:
        data = data[key]
    return data


class TestEachEditorHasAManifest:
    @pytest.mark.parametrize("label, rel, _", MANIFESTS, ids=[m[0] for m in MANIFESTS])
    def test_the_manifest_exists_and_parses(self, label, rel, _) -> None:
        assert _load(rel), f"{label}: empty manifest"

    def test_codex_declares_where_its_skills_and_server_are(self) -> None:
        """The two keys that make Codex able to do anything with it. Present-but-
        pointing-nowhere is the same as absent, so the targets are resolved."""
        manifest = _load("codex-plugin/.codex-plugin/plugin.json")

        assert manifest.get("skills"), "Codex will not find the skills"
        assert manifest.get("mcpServers"), "Codex will not start the server"

        # removeprefix, not lstrip: lstrip takes a CHARACTER SET, so
        # "./.codex/config.toml".lstrip("./") returns "codex/config.toml" and
        # eats the dot that makes .codex a real directory.
        skills = (REPO / "codex-plugin" / manifest["skills"].removeprefix("./")).resolve()
        server = (REPO / "codex-plugin" / manifest["mcpServers"].removeprefix("./")).resolve()
        assert skills.is_dir(), f"skills path does not exist: {skills}"
        assert server.is_file(), f"server config does not exist: {server}"

    def test_codex_has_the_block_its_plugin_list_renders(self) -> None:
        """Without ``interface`` the entry has no name, no description and no
        category to show, which is indistinguishable from not being there."""
        interface = _load("codex-plugin/.codex-plugin/plugin.json").get("interface")

        assert interface, "no interface block — nothing for the list to render"
        for key in ("displayName", "shortDescription", "developerName", "category"):
            assert interface.get(key), f"interface.{key} is empty"

    def test_the_marketplace_entry_carries_a_version(self) -> None:
        """The one that made every release look like no release."""
        entry = _load(".claude-plugin/marketplace.json")["plugins"][0]

        assert entry.get("version"), (
            "the marketplace entry has no version, so a client has nothing to "
            "compare and an installed plugin never looks out of date"
        )

    def test_the_marketplace_points_at_a_real_plugin(self) -> None:
        entry = _load(".claude-plugin/marketplace.json")["plugins"][0]
        source = (REPO / entry["source"]).resolve()

        assert source.is_dir(), f"marketplace source does not exist: {source}"
        assert (source / ".claude-plugin" / "plugin.json").is_file(), (
            "the marketplace points at a directory with no plugin manifest"
        )


class TestTheyAllAgreeOnTheVersion:
    def test_every_manifest_states_the_package_version(self) -> None:
        """Four places state it. Any one of them lagging is a plugin that
        advertises a release it is not."""
        # pyproject, not importlib.metadata: the installed distribution can be
        # an older release than this checkout, and then this test reports the
        # repository as inconsistent with itself. What is being checked is
        # whether the manifests in THIS tree agree with THIS tree's version.
        import re

        expected = re.search(
            r'^version\s*=\s*"([^"]+)"',
            (REPO / "pyproject.toml").read_text(encoding="utf-8"),
            re.MULTILINE,
        ).group(1)
        disagree = {}
        for label, rel, keys in MANIFESTS:
            found = str(_dig(_load(rel), keys))
            if found != expected:
                disagree[label] = found

        assert not disagree, (
            f"package is {expected}; these disagree: {disagree}"
        )


class TestSlmIsMoreThanSkills:
    """Agents, commands and hooks are part of the product, not extras.

    ``codex-plugin/`` shipped twelve skills and no agents and no commands, so a
    Codex user had a third of it; there was no Antigravity tree at all. Both were
    silent -- the directories that existed looked complete.
    """

    @pytest.mark.parametrize(
        "surface, tree",
        [
            ("Claude Code", "plugin"),
            ("Codex", "codex-plugin"),
            ("Antigravity", "antigravity-plugin"),
        ],
    )
    def test_agents_and_commands_ship_too(self, surface, tree) -> None:
        agents = list((REPO / tree).glob("agents/*.md"))
        commands = list((REPO / tree).glob("commands/*.md"))
        source_agents = list((REPO / "plugin-src").glob("agents/*.md"))
        source_commands = list((REPO / "plugin-src").glob("commands/*.md"))

        assert len(agents) == len(source_agents), (
            f"{surface}: {len(agents)} agents, source has {len(source_agents)}"
        )
        assert len(commands) == len(source_commands), (
            f"{surface}: {len(commands)} commands, source has {len(source_commands)}"
        )

    def test_vs_code_carries_the_agents_in_its_own_shape(self) -> None:
        found = list((REPO / "copilot-plugin/.github").glob("agents/*.md"))
        source = list((REPO / "plugin-src").glob("agents/*.md"))

        assert len(found) == len(source)


class TestTheSkillsActuallyArrive:
    @pytest.mark.parametrize(
        "label, pattern, minimum",
        [
            ("Claude Code", "plugin/skills/*/SKILL.md", 10),
            ("Codex", "codex-plugin/skills/*/SKILL.md", 10),
            ("VS Code", "copilot-plugin/.github/prompts/*.prompt.md", 10),
            ("Antigravity", "antigravity-plugin/skills/*/SKILL.md", 10),
        ],
    )
    def test_each_editor_gets_the_skills(self, label, pattern, minimum) -> None:
        found = list(REPO.glob(pattern))

        assert len(found) >= minimum, (
            f"{label}: only {len(found)} skills found at {pattern}"
        )
