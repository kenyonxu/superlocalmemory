# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A Hermes that has not granted SLM to the plugin says so, once, with the fix.

Hermes refuses every plugin MCP call unless ``config.yaml`` lists the server
under ``plugins.entries.superlocalmemory.mcp_allowlist``; it has no prompt for
it. The plugin caught that PermissionError like any failure, so on an install
without the line, recall, session start, tool logging and turn capture all did
nothing and nothing said why (the author's Hermes, 14 Oct 2026).
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "plugin-src" / "hermes"
FIX = "mcp_allowlist"


def _module():
    spec = importlib.util.spec_from_file_location("slm_hermes_grant", PLUGIN / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _Denying:
    def __init__(self) -> None:
        self.calls = 0

    def call_mcp(self, server, tool, arguments, timeout=0):
        self.calls += 1
        raise PermissionError(f"Plugin 'superlocalmemory' is not allowed to call MCP server {server!r}.")

    def get_config(self, key, default):
        return True if key == "capture_turns" else default


def test_a_denied_call_is_reported_once_with_the_fix(caplog):
    module = _module()
    plugin = module.SlmHermesPlugin(_Denying())
    with caplog.at_level(logging.WARNING):
        plugin.on_session_start(session_id="s1")
        assert plugin.pre_llm_call(session_id="s1", user_message="what did we decide?") is None
        plugin.post_llm_call(session_id="s1", user_message="hi there", assistant_response="hello")
    warnings = [r.getMessage() for r in caplog.records if FIX in r.getMessage()]
    assert len(warnings) == 1
    assert "plugins.entries.superlocalmemory.mcp_allowlist" in warnings[0]


def test_slm_status_names_the_missing_grant(monkeypatch):
    module = _module()
    plugin = module.SlmHermesPlugin(_Denying())
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/slm")
    monkeypatch.setattr(module, "_supported_slm", lambda binary: True)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"ok": true}', ""))
    out = plugin.slash_router("status")
    assert FIX in out and "slm upgrade-hosts --host hermes --apply" in out


def test_a_granted_plugin_status_is_unchanged(monkeypatch):
    module = _module()

    class Granted(_Denying):
        def call_mcp(self, server, tool, arguments, timeout=0):
            return {"ok": True, "result": "4.2.6"}

    plugin = module.SlmHermesPlugin(Granted())
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/slm")
    monkeypatch.setattr(module, "_supported_slm", lambda binary: True)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"ok": true}', ""))
    assert plugin.slash_router("status") == '{"ok": true}'
