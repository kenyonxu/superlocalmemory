# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Loading context is not a question: the answer-support gate does not run.

Session start, ``slm://context``, ``slm session-context --full`` and the context
file builder load a topic, not an answer, so they get their memories (under the
injection token cap) instead of "That isn't in SuperLocalMemory."
"""

from __future__ import annotations

from types import SimpleNamespace

from superlocalmemory.core import answer_check_scope as scope
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult

WEAK = {"hub_corrected": -0.2, "rare_miss": 1.0, "ce_raw": -11.0, "cosine": 0.4,
        "subject_coverage": 0.0}


def _response():
    out = RecallResponse(query="recent decisions", reranker_status="applied", results=[
        RetrievalResult(fact=AtomicFact(fact_id=f, memory_id=f"m-{f}", content=f"note {f}"),
                        score=0.7) for f in ("a", "b")])
    for r in out.results:
        r.evidence = dict(WEAK)
    out.query_evidence = {"latin_script": True, "nonsense_similarity": 0.45,
                          "orphan_terms": 1.0}
    return out


def _run(monkeypatch):
    from superlocalmemory.core import recall_pipeline
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    engine = SimpleNamespace(_sufficiency_judge=None, recall=lambda *a, **k: _response())
    monkeypatch.setattr(recall_pipeline, "apply_ranking", lambda resp, *a, **k: resp)
    # A stand-in store has no sources to check; the gate is what is under test.
    from superlocalmemory.retrieval import source_fidelity_flags
    monkeypatch.setattr(source_fidelity_flags, "flag_unverified",
                        lambda results, *a, **k: results)
    return recall_pipeline.run_recall(
        "recent decisions", "default", fast=True, config=SLMConfig.for_mode(Mode.A),
        retrieval_engine=engine, trust_scorer=None, embedder=None,
        db=SimpleNamespace(db_path=None), llm=None, hooks=None)


def test_a_question_is_gated_and_a_context_load_is_not(monkeypatch) -> None:
    assert _run(monkeypatch).results == []
    with scope.context_load():
        assert scope.answer_check_skipped() and scope.in_context_load()
        loaded = _run(monkeypatch)
    assert [r.fact.fact_id for r in loaded.results] == ["a", "b"]
    assert loaded.support_status == ""
    assert not scope.in_context_load()


def test_a_skipped_check_alone_is_still_gated(monkeypatch) -> None:
    # The per-prompt hook skips the answer check but injects supported answers only.
    with scope.skip_answer_check():
        assert _run(monkeypatch).results == []


def test_the_context_marker_crosses_http(monkeypatch) -> None:
    from superlocalmemory.cli import daemon
    from superlocalmemory.mcp import _daemon_proxy
    from superlocalmemory.server.recall_core import RecallCall, _engine_call

    sent: list[str] = []
    proxy = _daemon_proxy.DaemonPoolProxy.__new__(_daemon_proxy.DaemonPoolProxy)
    proxy._unavailable, proxy._timeout = False, 5.0
    monkeypatch.setattr(daemon, "daemon_request",
                        lambda method, path, **kw: sent.append(path) or {"ok": True})
    with scope.context_load():
        proxy.recall("q")
    proxy.recall("q", answer_check=False)
    assert "answer_check=context" in sent[0] and "answer_check=skip" in sent[1]
    assert scope.wants_skip("context") and scope.wants_context("context")
    seen: list = []
    engine = SimpleNamespace(recall=lambda q, **kw: seen.append(scope.in_context_load()))
    base = dict(query="q", limit=5, session_id="s", agent_id="a", fast=True)
    _engine_call(engine, RecallCall(**base, skip_answer_check=True, context_load=True))
    _engine_call(engine, RecallCall(**base, skip_answer_check=True))
    assert seen == [True, False]


def test_auto_recall_marks_session_context_but_not_a_query() -> None:
    from superlocalmemory.hooks.auto_recall import AutoRecall

    seen: list[bool] = []

    def recall_fn(query, limit):
        seen.append(scope.in_context_load())
        return _response()

    auto = AutoRecall(recall_fn=recall_fn, config={"enabled": True,
                                                   "relevance_threshold": 0.0})
    text = auto.get_session_context(query="recent decisions")
    auto.get_query_context("what did we decide")
    assert seen == [True, False]
    assert "note a" in text


def test_slm_context_says_why_when_nothing_matches() -> None:
    from superlocalmemory.mcp import resources

    assert "No session context available yet" not in resources.EMPTY_CONTEXT_NOTE
