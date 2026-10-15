# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Who gets the related tier: people searching by default, agents on request."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch


class _Server:
    def __init__(self) -> None:
        self.captured: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.captured[fn.__name__] = fn
            return fn
        return deco


def test_mcp_recall_sends_include_related_only_when_the_agent_asks(monkeypatch) -> None:
    from superlocalmemory.mcp import _daemon_proxy, tools_core

    seen: list[dict] = []

    class _Pool:
        def recall(self, *args, **kwargs):
            seen.append(kwargs)
            return {"ok": True, "results": [], "result_count": 0, "tier": "related"}

    monkeypatch.setattr(_daemon_proxy, "choose_pool", lambda: _Pool())
    server = _Server()
    tools_core.register_core_tools(server, lambda: SimpleNamespace(profile_id="default"))
    out = asyncio.run(server.captured["recall"]("q"))
    assert "include_related" not in seen[0] and out["tier"] == "related"
    asyncio.run(server.captured["recall"]("q", include_related=True))
    assert seen[1]["include_related"] is True


def test_the_daemon_proxy_and_recall_core_carry_it(monkeypatch) -> None:
    from superlocalmemory.cli import daemon
    from superlocalmemory.mcp import _daemon_proxy
    from superlocalmemory.server.recall_core import RecallCall, _engine_call

    sent: list[str] = []
    proxy = _daemon_proxy.DaemonPoolProxy.__new__(_daemon_proxy.DaemonPoolProxy)
    proxy._unavailable, proxy._timeout = False, 5.0
    monkeypatch.setattr(daemon, "daemon_request",
                        lambda method, path, **kw: sent.append(path) or {"ok": True})
    proxy.recall("q")
    proxy.recall("q", include_related=True)
    assert "include_related" not in sent[0] and "include_related=true" in sent[1]
    calls: list[dict] = []
    engine = SimpleNamespace(recall=lambda q, **kw: calls.append(kw))
    base = dict(query="q", limit=5, session_id="s", agent_id="a", fast=True)
    _engine_call(engine, RecallCall(**base))
    _engine_call(engine, RecallCall(**base, include_related=True))
    assert "include_related" not in calls[0] and calls[1]["include_related"] is True


def test_the_cli_always_asks_for_related_and_labels_it(capsys) -> None:
    from superlocalmemory.cli.commands import cmd_recall

    paths: list[str] = []

    def request(method, path, *a, **k):
        paths.append(path)
        return {"results": [{"content": "Alexa Morgan visited Kyoto", "score": 0.4,
                             "weak": True, "tier": "related"}],
                "abstention_note": "I don't have this exactly — here are the closest "
                                   "memories."}

    args = Namespace(query="Which university did Alexa Morgan attend?", limit=5, json=False,
                     fast=False, include_global=None, include_shared=None, window="", as_of="",
                     known_as_of="", valid_at="", include_unknown=False)
    with (patch("superlocalmemory.cli.daemon.is_daemon_running", return_value=True),
          patch("superlocalmemory.cli.daemon.ensure_daemon", return_value=True),
          patch("superlocalmemory.cli.daemon.daemon_request", side_effect=request)):
        cmd_recall(args)
    assert "include_related=true" in paths[0]
    out = capsys.readouterr().out
    assert "I don't have this exactly" in out and "[related] Alexa Morgan" in out


def test_the_dashboard_search_model_has_the_closest_matches_toggle() -> None:
    from superlocalmemory.server.routes.helpers import SearchRequest

    assert SearchRequest(query="q").show_weak is False
    assert SearchRequest(query="q", show_weak=True).show_weak is True
