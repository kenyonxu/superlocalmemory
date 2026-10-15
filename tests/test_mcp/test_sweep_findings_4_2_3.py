# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""What the end-to-end sweep of every MCP tool found (13 Oct 2026).

- skill_lineage on a store where no skill evolved: "no such table" as a failure.
- correct_pattern for a pattern that does not exist: success:true.
- review_correction for an unknown case: retryable:true, so an agent retries forever.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from superlocalmemory.mcp import tools_core, tools_evolution


class _Server:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _Auth:
    def __init__(self) -> None:
        self.completed = False

    def complete(self) -> None:
        self.completed = True


def test_skill_lineage_on_a_store_without_evolution_is_empty(monkeypatch, tmp_path):
    db = tmp_path / "memory.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(tools_evolution, "state_path", lambda name: db)
    monkeypatch.setattr(tools_evolution, "_skill_profile", lambda engine, pid: ("default", None))
    server = _Server()
    tools_evolution.register_evolution_tools(server, lambda: SimpleNamespace(profile_id="default"))
    out = asyncio.run(server.tools["skill_lineage"]())
    assert out["success"] is True and out["lineage"] == [] and "error" not in out


@pytest.fixture
def core(monkeypatch):
    server = _Server()
    engine = SimpleNamespace(profile_id="default", _db=SimpleNamespace(db_path=":memory:"))

    async def _profile(get_engine, profile_id):
        return "default", None

    monkeypatch.setattr(tools_core, "_call_profile", _profile)
    auth = _Auth()
    monkeypatch.setattr(tools_core, "authorize_mcp_mutation", lambda *a, **k: auth)
    tools_core.register_core_tools(server, lambda: engine)
    return server.tools, auth


def _store(monkeypatch, patterns):
    from superlocalmemory.learning import behavioral

    written = []

    class _Store:
        def __init__(self, path):
            pass

        def get_patterns(self, pid, limit=50, **kw):
            return patterns

        def record_pattern(self, pid, pattern_type, data):
            written.append(data)

    monkeypatch.setattr(behavioral, "BehavioralPatternStore", _Store)
    return written


def test_correct_pattern_refuses_an_unknown_pattern(core, monkeypatch):
    tools, auth = core
    written = _store(monkeypatch, [{"id": 7, "pattern_key": "hour_21"}])
    out = asyncio.run(tools["correct_pattern"]("nope", "wrong"))
    assert out["success"] is False and out["retryable"] is False
    assert written == [] and auth.completed


def test_correct_pattern_accepts_an_id_or_a_key(core, monkeypatch):
    tools, _ = core
    written = _store(monkeypatch, [{"id": 7, "pattern_key": "hour_21"}])
    assert asyncio.run(tools["correct_pattern"]("7", "evening person"))["success"] is True
    assert asyncio.run(tools["correct_pattern"]("hour_21", "evening person"))["success"] is True
    assert len(written) == 2


def test_review_correction_refused_is_not_retryable(core, monkeypatch):
    from superlocalmemory.cli import daemon

    tools, _ = core
    monkeypatch.setattr(daemon, "is_daemon_running", lambda: True)

    def refuse(*a, **k):
        assert k.get("preserve_not_found") and k.get("preserve_conflict")
        raise daemon.not_found_from({"detail": "no such correction case"}, "/api/corrections/x/apply")

    monkeypatch.setattr(daemon, "daemon_request", refuse)
    out = asyncio.run(tools["review_correction"]("missing-case", "apply", expected_version=1))
    assert out["success"] is False and out["retryable"] is False
    assert "refused" in out["error"]


def test_review_correction_no_answer_is_retryable(core, monkeypatch):
    from superlocalmemory.cli import daemon

    tools, _ = core
    monkeypatch.setattr(daemon, "is_daemon_running", lambda: True)
    monkeypatch.setattr(daemon, "daemon_request", lambda *a, **k: None)
    out = asyncio.run(tools["review_correction"]("case", "apply", expected_version=1))
    assert out["success"] is False and out["retryable"] is True
