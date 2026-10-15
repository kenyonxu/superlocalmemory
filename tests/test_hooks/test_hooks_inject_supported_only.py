# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Hooks and auto-inject put only SUPPORTED memories into an agent's context.

A recall the answer-support gate judged related (the closest memories),
abstained on (nonsense, nothing close) or ran while warming up injects
nothing: no block, no closest list. The token cap still holds. An explicit
``recall`` call is unchanged and keeps all three tiers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from superlocalmemory.core import injection
from superlocalmemory.hooks import auto_recall_hook as hook


def _item(fid, tier, content=None):
    return {"fact_id": fid, "content": content or f"memory {fid} about the build",
            "score": 0.8, "tier": tier}


def _resp(tier, *, warming=False, items=()):
    return {"results": list(items), "tier": tier, "warming_up": warming,
            "support": {"status": "supported" if tier == "supported" else "unsupported",
                        "tier": tier},
            "abstained": tier != "supported"}


@pytest.fixture(autouse=True)
def _mode(monkeypatch):
    monkeypatch.setattr(hook, "_detect_mode", lambda: "A")


def _context(items, response):
    out = hook._format_envelope(list(items), response=response)
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_supported_results_are_injected_and_weak_ones_are_not() -> None:
    items = [_item("a", "supported"), _item("b", "weak")]
    text = _context(items, _resp("supported", items=items))
    assert "memory a" in text and "memory b" not in text


def test_the_closest_memories_inject_nothing() -> None:
    items = [_item("a", "related"), _item("b", "related")]
    assert hook._format_envelope(items, response=_resp("related", items=items)) == {}


def test_an_abstained_recall_injects_nothing_not_even_a_pinned_memory() -> None:
    pinned = [_item("p", "weak")]
    assert hook._format_envelope(pinned, response=_resp("none", items=pinned)) == {}


def test_while_warming_up_nothing_unchecked_is_injected() -> None:
    items = [_item("a", "related")]
    assert hook._format_envelope(items, response=_resp("related", warming=True,
                                                       items=items)) == {}
    # Even a result labelled supported is not trusted while inputs were missing.
    sup = [_item("a", "supported")]
    assert hook._format_envelope(sup, response=_resp("supported", warming=True,
                                                     items=sup)) == {}


def test_the_token_cap_still_holds_on_supported_results() -> None:
    big = [_item(str(i), "supported", "word " * 2000) for i in range(12)]
    text = _context(big, _resp("supported", items=big))
    cap = injection.resolve_budget("A", None)
    assert text and injection.estimate_tokens(text) <= cap + 64  # wrapper lines


def test_a_recall_the_gate_never_judged_keeps_the_old_behaviour() -> None:
    """The gate's kill switch (or a daemon from before it): no tier anywhere."""
    items = [{"fact_id": "a", "content": "memory a", "score": 0.8}]
    assert "memory a" in _context(items, {"results": items})


def test_the_hook_daemon_socket_injects_supported_only(monkeypatch, tmp_path) -> None:
    from superlocalmemory.hooks.hook_daemon import HookDaemon

    daemon = HookDaemon(sock_path=tmp_path / "s.sock", queue_db_path=tmp_path / "q.db")
    related = [_item("a", "related")]
    daemon._queue = SimpleNamespace(
        enqueue=lambda **k: "r1",
        poll_result=lambda *a, **k: _resp("related", items=related))
    monkeypatch.setattr(hook, "_switched_off", lambda: False)
    assert daemon._process_request({"prompt": "which database do we use"}) == {}


def test_auto_recall_query_context_returns_supported_only() -> None:
    from superlocalmemory.hooks.auto_recall import AutoRecall
    from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult

    def result(fid, tier):
        r = RetrievalResult(fact=AtomicFact(fact_id=fid, content=f"memory {fid}"), score=0.9)
        r.tier = tier
        return r

    def recall_fn(query, limit=10, response=RecallResponse):
        out = RecallResponse(query=query, results=[result("a", "supported"),
                                                   result("b", "weak")])
        out.support_tier = "supported"
        out.support_status = "supported"
        return out

    auto = AutoRecall(recall_fn=recall_fn)
    assert [m["fact_id"] for m in auto.get_query_context("what db")] == ["a"]

    def related_fn(query, limit=10):
        out = RecallResponse(query=query, results=[result("a", "related")])
        out.support_tier, out.support_status = "related", "unsupported"
        return out

    assert AutoRecall(recall_fn=related_fn).get_query_context("what db") == []

    def warming_fn(query, limit=10):
        out = related_fn(query)
        out.warming_up = True
        return out

    assert AutoRecall(recall_fn=warming_fn).get_query_context("what db") == []


def test_render_context_drops_unsupported_in_every_mode(monkeypatch) -> None:
    """The shared renderer is the choke point: even the legacy renderer obeys."""
    mems = [injection.InjectableMemory(content="kept fact one", score=0.9, fact_id="k",
                                       support_tier="supported"),
            injection.InjectableMemory(content="closest fact two", score=0.9, fact_id="r",
                                       support_tier="related"),
            injection.InjectableMemory(content="pinned fact three", score=0.0, fact_id="p")]
    for legacy in ("0", "1"):
        monkeypatch.setenv("SLM_INJECTION_LEGACY", legacy)
        text = injection.render_context(mems, mode="B", cfg=None, wrap=True)
        assert "kept fact one" in text and "pinned fact three" in text
        assert "closest fact two" not in text
