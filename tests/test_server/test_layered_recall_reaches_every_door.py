# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""depth and max_tokens reach recall from MCP, the daemon and the worker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


class _Server:
    def __init__(self) -> None:
        self.captured: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.captured[fn.__name__] = fn
            return fn
        return deco


def test_mcp_recall_validates_and_forwards_depth_and_budget(monkeypatch) -> None:
    from superlocalmemory.mcp import _daemon_proxy, tools_core

    seen: list[dict] = []

    class _Pool:
        def recall(self, *args, **kwargs):
            seen.append(kwargs)
            return {"ok": True, "results": [], "result_count": 0}

    monkeypatch.setattr(_daemon_proxy, "choose_pool", lambda: _Pool())
    server = _Server()
    tools_core.register_core_tools(server, lambda: SimpleNamespace(profile_id="default"))
    asyncio.run(server.captured["recall"]("q"))
    assert "depth" not in seen[0] and "max_tokens" not in seen[0]
    asyncio.run(server.captured["recall"]("q", depth=0, max_tokens=300))
    assert seen[1]["depth"] == 0 and seen[1]["max_tokens"] == 300
    bad = asyncio.run(server.captured["recall"]("q", depth=3))
    assert bad["code"] == "INVALID_LAYERS"


def test_the_daemon_envelope_shapes_within_the_budget() -> None:
    from superlocalmemory.core.recall_layers import tokens_of
    from superlocalmemory.server.recall_core import RecallCall, _layers

    engine = SimpleNamespace(profile_id="default", _db=None)
    items = [{"fact_id": f"f{i}", "content": "word " * 200} for i in range(5)]
    base = dict(query="q", limit=5, session_id="s", agent_id="a", fast=True)
    same, report = _layers(engine, RecallCall(**base), list(items))
    assert same == items and report == {}
    shaped, report = _layers(engine, RecallCall(**base, depth=0, max_tokens=120), items)
    assert tokens_of(shaped) <= 120 and report["depth"] == 0


def test_the_proxy_and_worker_pool_send_layers_only_when_set(monkeypatch) -> None:
    from superlocalmemory.cli import daemon
    from superlocalmemory.core.worker_pool import WorkerPool
    from superlocalmemory.mcp import _daemon_proxy

    sent: list[str] = []
    proxy = _daemon_proxy.DaemonPoolProxy.__new__(_daemon_proxy.DaemonPoolProxy)
    proxy._unavailable, proxy._timeout = False, 5.0
    monkeypatch.setattr(daemon, "daemon_request",
                        lambda method, path, **kw: sent.append(path) or {"ok": True})
    proxy.recall("q")
    proxy.recall("q", depth=2, max_tokens=500)
    assert "depth" not in sent[0] and "depth=2" in sent[1] and "max_tokens=500" in sent[1]
    pool = WorkerPool.__new__(WorkerPool)
    msgs: list[dict] = []
    monkeypatch.setattr(pool, "_send", lambda msg: msgs.append(msg) or {"ok": True},
                        raising=False)
    pool.recall("q", depth=0, max_tokens=50)
    assert msgs[0]["depth"] == 0 and msgs[0]["max_tokens"] == 50


def test_cli_sends_depth_and_budget_and_prints_context(capsys) -> None:
    from argparse import Namespace
    from unittest.mock import patch

    from superlocalmemory.cli.commands import cmd_recall

    paths: list[str] = []

    def request(method, path, *a, **k):
        paths.append(path)
        return {"results": [{"content": "Kestrel uses PostgreSQL 16.", "score": 0.9,
                             "context": {"source": "Kestrel notes", "same_session": [
                                 {"content": "Backups run daily."}], "same_entities": [],
                                 "superseded_by": None}}]}

    args = Namespace(query="kestrel db", limit=5, json=False, fast=False, include_global=None,
                     include_shared=None, window="", as_of="", known_as_of="", valid_at="",
                     include_unknown=False, depth=2, max_tokens=400)
    with (patch("superlocalmemory.cli.daemon.is_daemon_running", return_value=True),
          patch("superlocalmemory.cli.daemon.ensure_daemon", return_value=True),
          patch("superlocalmemory.cli.daemon.daemon_request", side_effect=request)):
        cmd_recall(args)
    assert "depth=2" in paths[0] and "max_tokens=400" in paths[0]
    out = capsys.readouterr().out
    assert "source: Kestrel notes" in out and "same session: Backups run daily." in out
