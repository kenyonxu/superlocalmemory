# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Evidence features are measured, kept, and change nothing about recall."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from superlocalmemory.retrieval import evidence_features as ef
from superlocalmemory.retrieval.engine import RetrievalEngine
from superlocalmemory.retrieval.fusion import FusionResult
from superlocalmemory.storage.models import RecallResponse, RetrievalResult


def test_query_terms_keep_identifiers_whole_and_drop_question_words() -> None:
    assert ef.query_terms("How long does ensure_daemon wait for v0.3.4?") == [
        "long", "ensure_daemon", "wait", "v0.3.4"]
    assert ef.query_terms("What is the the THE?") == []


def test_keyword_coverage_matches_whole_pieces_of_an_identifier() -> None:
    content = "Payment receipt: app id asdk_app_6ac9e8f2, status succeeded."
    assert ef.keyword_coverage(["asdk_app_6ac9e8f2"], content) == 1.0
    assert ef.keyword_coverage(["asdk", "wimble", "frobnicate"], content) == pytest.approx(1 / 3)
    assert ef.keyword_coverage(["asdkjh"], content) == 0.0  # a prefix is not a term
    assert ef.keyword_coverage([], content) == 0.0


def test_entity_hit_needs_the_whole_name() -> None:
    assert ef.entity_hit("Who is Priya Sen?", ["Priya Sen"])
    assert not ef.entity_hit("Who is Priyanka?", ["Priya"])
    assert not ef.entity_hit("anything", ["", "x"])


def test_bm25_raw_inverts_the_channel_saturation() -> None:
    for raw in (0.5, 2.676, 10.15):
        unit = raw / (raw + 5.0)
        assert ef.bm25_raw(unit) == pytest.approx(raw)
    assert ef.bm25_raw(0.0) == 0.0
    assert ef.bm25_raw(None) is None
    assert ef.bm25_raw(1.0) is None


def test_cosine_handles_missing_and_mismatched_vectors() -> None:
    assert ef.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert ef.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert ef.cosine(None, [1.0]) is None
    assert ef.cosine([1.0], [1.0, 2.0]) is None
    assert ef.cosine([0.0, 0.0], [1.0, 1.0]) is None


def test_build_reports_every_feature_and_the_hub_correction() -> None:
    fact = SimpleNamespace(content="ensure_daemon waits 12 seconds", embedding=[1.0, 0.0],
                           canonical_entities=["ensure_daemon"], entities=[])
    out = ef.build(query="ensure_daemon wait", terms=ef.query_terms("ensure_daemon wait"),
                   query_vector=[1.0, 0.0], fact=fact, channel_scores={"bm25": 0.5},
                   ce_raw=3.2, ce_batch_norm=1.0, hub_baseline=0.4, hub_baseline_mean=0.3)
    assert out["cosine"] == pytest.approx(1.0)
    assert out["hub_corrected"] == pytest.approx(0.6)
    assert out["hub_corrected_mean"] == pytest.approx(0.7)
    assert out["bm25_raw"] == pytest.approx(5.0)
    assert out["keyword_coverage"] == pytest.approx(0.5)  # "wait" is not "waits"
    assert out["entity_hit"] is True
    assert out["ce_raw"] == 3.2 and out["ce_batch_norm"] == 1.0
    empty = ef.build(query="q", terms=[], query_vector=None, fact=SimpleNamespace(),
                     channel_scores=None)
    assert empty["cosine"] is None and empty["hub_corrected"] is None
    assert empty["bm25_raw"] == 0.0


def test_query_evidence_calls_the_nonsense_check(monkeypatch) -> None:
    from superlocalmemory.retrieval import query_lexicon

    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: frozenset({"purple", "bananas"}))
    assert ef.query_evidence("zxqv plorth wimble")["oov_phrase"] is True
    assert ef.query_evidence("purple elephants juggling")["oov_phrase"] is False
    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: None)
    q = ef.query_evidence("zxqv plorth wimble")
    assert q["oov_phrase"] is False and q["vocabulary_available"] is False
    assert q["query_terms"] == 3


class _Reranker:
    def rerank(self, query, candidates, top_k):
        table = {"a": 4.0, "b": -2.0, "c": 1.0}
        return sorted(((f, table[f.fact_id]) for f, _ in candidates), key=lambda t: -t[1])


def _engine(reranker=None):
    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._reranker = reranker
    return eng


def test_reranker_hands_back_raw_and_batch_scores_without_changing_the_order() -> None:
    fused = [FusionResult("a", 0.3), FusionResult("b", 0.9), FusionResult("c", 0.5)]
    facts = {k: SimpleNamespace(fact_id=k, content=f"note {k}") for k in "abc"}
    plain, applied, _ = _engine(_Reranker())._apply_reranker("q", list(fused), dict(facts))
    raw: dict = {}
    kept, applied2, _ = _engine(_Reranker())._apply_reranker(
        "q", list(fused), dict(facts), raw_out=raw)
    assert applied and applied2
    assert [r.fact_id for r in kept] == [r.fact_id for r in plain]
    assert [r.fused_score for r in kept] == [r.fused_score for r in plain]
    assert raw == {"a": (4.0, 1.0), "b": (-2.0, 0.0), "c": (1.0, pytest.approx(0.5))}


def _result(fid, content, embedding):
    fact = SimpleNamespace(fact_id=fid, content=content, embedding=embedding,
                           canonical_entities=[], entities=[])
    return RetrievalResult(fact=fact, score=0.7, channel_scores={"semantic": 0.9},
                           ranking_score=1.2)


def test_attach_evidence_records_features_and_leaves_scores_alone(monkeypatch) -> None:
    eng = _engine()
    eng._query_embedding_cache = {"ensure_daemon wait": [1.0, 0.0]}
    eng._hub_baselines = lambda ids, profile: {"f1": (0.5, 0.25)}
    results = [_result("f1", "ensure_daemon waits", [1.0, 0.0]),
               _result("f2", "unrelated receipt", [0.0, 1.0])]
    before = [(r.fact.fact_id, r.score, r.ranking_score) for r in results]
    q = eng._attach_evidence("ensure_daemon wait", results, {"f1": (2.5, 1.0)}, "default")
    assert [(r.fact.fact_id, r.score, r.ranking_score) for r in results] == before
    assert results[0].evidence["hub_corrected"] == pytest.approx(0.5)
    assert results[0].evidence["ce_raw"] == 2.5
    assert results[1].evidence["ce_raw"] is None
    assert results[1].evidence["hub_baseline"] is None
    assert q["query_terms"] == 2


def test_attach_evidence_never_raises() -> None:
    eng = _engine()  # no caches, no database: every feature missing, nothing raised

    def boom(*_a):
        raise RuntimeError("db down")

    eng._hub_baselines = boom
    results = [_result("f1", "x", None)]
    assert isinstance(eng._attach_evidence("q", results, {}, "default"), dict)
    assert results[0].evidence == {}


def test_recall_trace_shows_evidence_but_an_ordinary_recall_does_not() -> None:
    from superlocalmemory.server.recall_serializer import serialize_recall_response

    r = _result("f1", "ensure_daemon waits", [1.0, 0.0])
    r.fact.memory_id = "m1"
    r.evidence = {"cosine": 0.9, "entity_hit": False}
    response = RecallResponse(query="q", results=[r], query_evidence={"oov_phrase": False})
    plain, _ = serialize_recall_response(response, limit=5)
    traced, _ = serialize_recall_response(response, limit=5, include_evidence=True)
    assert "evidence" not in plain[0]
    assert traced[0]["evidence"] == {"cosine": 0.9, "entity_hit": False}
    assert math.isclose(traced[0]["score"], plain[0]["score"])
