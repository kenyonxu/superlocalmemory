# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""One saved choice of MCP tool set, read by every ``slm mcp`` (14 Oct 2026).

Plugins and extensions start ``slm mcp`` from host configs a user should not
have to edit. ``slm tools set <name>`` saves the tool set in SLM's own data
folder; every ``slm mcp`` without an explicit ``SLM_MCP_PROFILE`` reads it.
The default stays ``full`` (memory, mesh, optimize), which fits hosts that cap
the total number of MCP tools at about 100.
"""

from __future__ import annotations

import json

import pytest

from superlocalmemory.mcp.profile_names import (
    UnknownProfileError,
    effective_profile,
    save_tool_set,
    saved_tool_set,
)


def test_nothing_saved_means_the_default(tmp_path):
    assert saved_tool_set(tmp_path) == ""
    assert effective_profile({}, tmp_path) == ""


def test_a_saved_choice_applies_when_the_host_sets_none(tmp_path):
    save_tool_set("power", tmp_path)
    assert json.loads((tmp_path / "mcp_tools.json").read_text())["tool_set"] == "power"
    assert effective_profile({}, tmp_path) == "power"


def test_the_hosts_own_setting_wins(tmp_path):
    save_tool_set("power", tmp_path)
    assert effective_profile({"SLM_MCP_PROFILE": "core"}, tmp_path) == "core"


def test_default_clears_the_saved_choice(tmp_path):
    save_tool_set("whole", tmp_path)
    save_tool_set("default", tmp_path)
    assert effective_profile({}, tmp_path) == ""


def test_unknown_names_are_refused_and_a_bad_file_never_widens(tmp_path):
    with pytest.raises(UnknownProfileError):
        save_tool_set("everything", tmp_path)
    (tmp_path / "mcp_tools.json").write_text('{"tool_set": "everything"}')
    assert effective_profile({}, tmp_path) == ""
    (tmp_path / "mcp_tools.json").write_text("not json")
    assert effective_profile({}, tmp_path) == ""


def test_slm_tools_shows_and_sets(tmp_path, monkeypatch, capsys):
    from argparse import Namespace

    from superlocalmemory.cli.tools_cmd import cmd_tools

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SLM_MCP_PROFILE", raising=False)
    cmd_tools(Namespace(action="set", name="power", json=False))
    out = capsys.readouterr().out
    assert "power" in out and "restart" in out.lower()
    cmd_tools(Namespace(action=None, name=None, json=True))
    shown = json.loads(capsys.readouterr().out)
    assert shown["tool_set"] == "power" and shown["source"] == "saved"
    assert shown["counts"]["full"] < shown["counts"]["power"]
