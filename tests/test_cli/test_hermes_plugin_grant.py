# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``slm upgrade-hosts --host hermes`` grants SLM to the Hermes plugin.

Hermes refuses a plugin's MCP calls unless config.yaml lists the server under
``plugins.entries.<plugin>.mcp_allowlist``, and it has no prompt for it (its
documented grant is that line). The install guide said "Hermes asks", it never
did, and upgrade-hosts reported such a Hermes as "verified": the plugin's
recall, session tracking and turn capture did nothing on installs that followed
the guide (14 Oct 2026).
"""

from __future__ import annotations

import yaml

from superlocalmemory.cli.host_upgrades import hermes_plugin_grant

BASE = {
    "model": {"default": "gpt-x"},
    "mcp_servers": {"superlocalmemory": {"command": "slm", "args": ["mcp"]}},
    "plugins": {"enabled": ["superlocalmemory", "other"],
                "entries": {"other": {"allow_tool_override": False}}},
}


def _write(tmp_path, data, text=None):
    path = tmp_path / "config.yaml"
    path.write_text(text if text is not None else yaml.safe_dump(data, sort_keys=False),
                    encoding="utf-8")
    return path


def _entries(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["plugins"]["entries"]


def test_apply_adds_the_grant_and_keeps_everything_else(tmp_path):
    path = _write(tmp_path, BASE)
    out = hermes_plugin_grant(path, apply=True)
    assert out["status"] == "updated"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["plugins"]["entries"]["superlocalmemory"]["mcp_allowlist"] == ["superlocalmemory"]
    assert data["plugins"]["entries"]["other"] == {"allow_tool_override": False}
    assert {k: v for k, v in data.items() if k != "plugins"} == {
        k: v for k, v in BASE.items() if k != "plugins"}
    assert list(tmp_path.glob("config.yaml.bak-slm-*")), "a backup is kept"


def test_preview_names_the_fix_and_writes_nothing(tmp_path):
    path = _write(tmp_path, BASE)
    before = path.read_text(encoding="utf-8")
    out = hermes_plugin_grant(path, apply=False)
    assert out["status"] == "preview" and "mcp_allowlist" in out["detail"]
    assert path.read_text(encoding="utf-8") == before


def test_an_existing_allowlist_is_extended_not_replaced(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE))
    data["plugins"]["entries"]["superlocalmemory"] = {"mcp_allowlist": ["github"],
                                                      "settings": {"capture_turns": True}}
    path = _write(tmp_path, data)
    hermes_plugin_grant(path, apply=True)
    entry = _entries(path)["superlocalmemory"]
    assert entry["mcp_allowlist"] == ["github", "superlocalmemory"]
    assert entry["settings"] == {"capture_turns": True}


def test_an_already_granted_plugin_is_verified_untouched(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE))
    data["plugins"]["entries"]["superlocalmemory"] = {"mcp_allowlist": ["superlocalmemory"]}
    path = _write(tmp_path, data)
    before = path.read_text(encoding="utf-8")
    assert hermes_plugin_grant(path, apply=True)["status"] == "verified"
    assert path.read_text(encoding="utf-8") == before


def test_a_hand_commented_config_is_never_rewritten(tmp_path):
    text = yaml.safe_dump(BASE, sort_keys=False) + "# my notes about this setup\n"
    path = _write(tmp_path, None, text)
    out = hermes_plugin_grant(path, apply=True)
    assert out["status"] == "blocked" and "mcp_allowlist" in out["detail"]
    assert path.read_text(encoding="utf-8") == text


def test_a_hermes_without_the_plugin_is_left_alone(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE))
    data["plugins"]["enabled"] = ["other"]
    path = _write(tmp_path, data)
    assert hermes_plugin_grant(path, apply=True)["status"] == "not-installed"
    assert "superlocalmemory" not in _entries(path)


def test_no_hermes_config_is_not_installed(tmp_path):
    assert hermes_plugin_grant(tmp_path / "config.yaml", apply=True)["status"] == "not-installed"


def test_doctor_warns_with_the_fix_when_the_grant_is_missing(tmp_path, monkeypatch):
    from superlocalmemory.cli.host_upgrades import hermes_plugin_finding

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write(tmp_path, BASE)
    status, detail, fix = hermes_plugin_finding()
    assert status == "WARN" and "turn capture" in detail
    assert fix == "slm upgrade-hosts --host hermes --apply"


def test_doctor_passes_a_granted_plugin_and_skips_no_hermes(tmp_path, monkeypatch):
    from superlocalmemory.cli.host_upgrades import hermes_plugin_finding

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert hermes_plugin_finding() is None
    data = yaml.safe_load(yaml.safe_dump(BASE))
    data["plugins"]["entries"]["superlocalmemory"] = {"mcp_allowlist": ["superlocalmemory"]}
    _write(tmp_path, data)
    assert hermes_plugin_finding()[0] == "PASS"
