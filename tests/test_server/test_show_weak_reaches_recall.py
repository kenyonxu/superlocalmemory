# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``show_weak`` reaches the engine from every door, and only when asked.

When nothing in memory supports a question, recall returns nothing; the MCP
``show_weak`` argument, ``slm recall --show-weak`` and ``GET
/recall?show_weak=true`` ask for the weak matches instead. Unset, every
request is exactly what it was before.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from superlocalmemory.mcp import tools_core


class _Server:
    def __init__(self) -> None:
        self.captured: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.captured[fn.__name__] = fn
            return fn
        return deco


def _mcp_recall(monkeypatch):
    from superlocalmemory.mcp import _daemon_proxy

    seen: list[dict] = []

    class _Pool:
        def recall(self, *args, **kwargs):
            seen.append(kwargs)
            return {"ok": True, "results": [], "result_count": 0,
                    "abstention_note": "Nothing in memory supports this question."}

    monkeypatch.setattr(_daemon_proxy, "choose_pool", lambda: _Pool())
    server = _Server()
    tools_core.register_core_tools(server, lambda: SimpleNamespace(profile_id="default"))
    return server.captured["recall"], seen


def test_mcp_recall_forwards_show_weak_only_when_set_and_returns_the_note(monkeypatch) -> None:
    tool, seen = _mcp_recall(monkeypatch)
    out = asyncio.run(tool("zxqv plorth wimble"))
    assert "show_weak" not in seen[0]
    assert out["abstention_note"] == "Nothing in memory supports this question."
    asyncio.run(tool("zxqv plorth wimble", show_weak=True))
    assert seen[1]["show_weak"] is True


def test_daemon_proxy_sends_show_weak_only_when_set(monkeypatch) -> None:
    from superlocalmemory.cli import daemon
    from superlocalmemory.mcp import _daemon_proxy

    sent: list[str] = []
    proxy = _daemon_proxy.DaemonPoolProxy.__new__(_daemon_proxy.DaemonPoolProxy)
    proxy._unavailable = False
    proxy._timeout = 5.0
    monkeypatch.setattr(daemon, "daemon_request",
                        lambda method, path, **kw: sent.append(path) or {"ok": True})
    proxy.recall("q")
    proxy.recall("q", show_weak=True)
    assert "show_weak" not in sent[0]
    assert "show_weak=true" in sent[1]


def test_recall_core_passes_show_weak_to_the_engine() -> None:
    from superlocalmemory.server.recall_core import RecallCall, _engine_call

    calls: list[dict] = []
    engine = SimpleNamespace(recall=lambda q, **kw: calls.append(kw) or "response")
    base = dict(query="q", limit=5, session_id="s", agent_id="a", fast=True)
    _engine_call(engine, RecallCall(**base))
    _engine_call(engine, RecallCall(**base, show_weak=True))
    assert "show_weak" not in calls[0]
    assert calls[1]["show_weak"] is True


def test_worker_pool_and_worker_carry_show_weak(monkeypatch) -> None:
    from superlocalmemory.core import recall_worker
    from superlocalmemory.core.worker_pool import WorkerPool

    pool = WorkerPool.__new__(WorkerPool)
    msgs: list[dict] = []
    monkeypatch.setattr(pool, "_send", lambda msg: msgs.append(msg) or {"ok": True},
                        raising=False)
    pool.recall("q")
    pool.recall("q", show_weak=True)
    assert "show_weak" not in msgs[0] and msgs[1]["show_weak"] is True

    seen: list[dict] = []

    class _Engine:
        _db = SimpleNamespace(get_memory_content_batch=lambda *a, **k: {})
        profile_id = "default"
        _config = SimpleNamespace(retrieval=None)

        def recall(self, query, **kw):
            seen.append(kw)
            from superlocalmemory.storage.models import RecallResponse
            return RecallResponse(query=query)

    monkeypatch.setattr(recall_worker, "_get_engine", lambda: _Engine())
    recall_worker._handle_recall("q", 5, show_weak=True)
    assert seen[0]["show_weak"] is True


def test_cli_recall_text_prints_the_note_and_labels_weak_results() -> None:
    from superlocalmemory.cli.recall_text import empty_result_line, result_line

    note = "Nothing in memory supports this question."
    assert empty_result_line({"results": [], "abstention_note": note}) == note
    assert empty_result_line({"results": [], "no_confident_match": True}) == "No confident match."
    assert result_line(1, {"score": 0.61, "content": "rcpt ok", "weak": True}) == \
        "  1. [0.61] [weak] rcpt ok"
    assert result_line(2, {"score": None, "content": "note"}) == "  2. [0.00] note"


def test_cli_recall_sends_show_weak_and_prints_the_note(capsys) -> None:
    from argparse import Namespace
    from unittest.mock import patch

    from superlocalmemory.cli.commands import cmd_recall

    note = "Nothing in memory supports this question."
    paths: list[str] = []

    def request(method, path, *a, **k):
        paths.append(path)
        weak = "show_weak=true" in path
        return {"results": [{"content": "rcpt ok", "score": 0.6, "weak": True}] if weak else [],
                "abstention_note": note + (" These are the closest weak matches." if weak else "")}

    args = dict(query="zxqv", limit=5, json=False, fast=False, include_global=None,
                include_shared=None, window="", as_of="", known_as_of="", valid_at="",
                include_unknown=False)
    with (patch("superlocalmemory.cli.daemon.is_daemon_running", return_value=True),
          patch("superlocalmemory.cli.daemon.ensure_daemon", return_value=True),
          patch("superlocalmemory.cli.daemon.daemon_request", side_effect=request)):
        cmd_recall(Namespace(**args))
        cmd_recall(Namespace(**args, show_weak=True))
    assert "show_weak" not in paths[0] and "show_weak=true" in paths[1]
    out = capsys.readouterr().out
    assert out.count(note) == 2 and "[weak] rcpt ok" in out


def test_the_daemons_deadline_bounds_the_answer_check() -> None:
    from superlocalmemory.retrieval import answer_check_status as acs
    from superlocalmemory.server.recall_core import RecallCall, _engine_call

    seen: list = []
    engine = SimpleNamespace(recall=lambda q, **kw: seen.append(acs._CALLER_DEADLINE.get()))
    base = dict(query="q", limit=5, session_id="s", agent_id="a", fast=True)
    _engine_call(engine, RecallCall(**base, answer_deadline=123.0))
    _engine_call(engine, RecallCall(**base))
    assert seen == [123.0, None]
