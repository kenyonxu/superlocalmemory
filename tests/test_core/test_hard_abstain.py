# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""When nothing in memory supports the question, recall returns nothing.

The answer-support gate (``retrieval/support_gate.py``) decides; the score
contract applies it. Pinned memories stay, an explicit filter keeps today's
behaviour, and ``show_weak`` shows the weak matches labelled as weak.
"""

from __future__ import annotations

from types import SimpleNamespace

from superlocalmemory.core.score_contract import finalize_score_contract
from superlocalmemory.retrieval import answerability as ans
from superlocalmemory.retrieval import support_gate as sg
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult


def _result(fid: str, *, pinned: bool = False) -> RetrievalResult:
    fact = AtomicFact(fact_id=fid, memory_id=f"m-{fid}", content=f"note {fid}",
                      pinned=pinned)
    return RetrievalResult(fact=fact, score=0.7, channel_scores={"semantic": 0.8})


def _response(*fids: str, pinned: tuple[str, ...] = ()) -> RecallResponse:
    return RecallResponse(query="q", reranker_status="applied",
                          results=[_result(f, pinned=f in pinned) for f in fids])


def _support(*probs: float, threshold: float = 0.5) -> sg.Assessment:
    best = max(probs) if probs else None
    ok = best is not None and best >= threshold
    return sg.Assessment(supported=ok, best=best, threshold=threshold, probabilities=probs,
                         version="test", reason=None if ok else sg.REASON_UNSUPPORTED)


def _verdict(insufficient: bool):
    return SimpleNamespace(calibration_status="measured", calibration_id="laya@x",
                           answer_confidence=0.3 if insufficient else 0.9,
                           insufficient=insufficient)


def test_unsupported_recall_returns_nothing_with_a_plain_reason() -> None:
    r = finalize_score_contract(_response("a", "b"), support=_support(0.2, 0.1))
    assert r.results == []
    assert r.abstained and r.abstention_reason == "unsupported"
    assert r.abstention_note == sg.ABSTAIN_NOTE
    assert r.support_status == "unsupported" and r.no_confident_match
    assert ans.of_response(r) == (ans.UNSUPPORTED, ans.REASON_NO_SUPPORT)


def test_pinned_memories_stay_when_recall_abstains() -> None:
    r = finalize_score_contract(_response("a", "p", pinned=("p",)), support=_support(0.2, 0.1))
    assert [x.fact.fact_id for x in r.results] == ["p"]
    assert r.abstained and r.abstention_reason == "unsupported"
    assert r.results[0].weak and r.results[0].rank_position == 1


def test_show_weak_returns_the_weak_matches_labelled() -> None:
    r = finalize_score_contract(_response("a", "b"), support=_support(0.2, 0.1),
                                show_weak=True)
    assert [x.fact.fact_id for x in r.results] == ["a", "b"]
    assert all(x.weak for x in r.results)
    assert [x.support for x in r.results] == [0.2, 0.1]
    assert r.abstained and r.weak_shown
    assert r.abstention_note.startswith(sg.ABSTAIN_NOTE) and "weak" in r.abstention_note


def test_an_explicit_filter_keeps_its_results_and_says_they_are_weak() -> None:
    r = finalize_score_contract(_response("a"), support=_support(0.2), keep_results=True)
    assert [x.fact.fact_id for x in r.results] == ["a"]
    assert r.abstained and r.results[0].weak and r.abstention_note


def test_supported_recall_keeps_every_result_and_labels_each() -> None:
    r = finalize_score_contract(_response("a", "b"), support=_support(0.9, 0.2))
    assert [x.fact.fact_id for x in r.results] == ["a", "b"]
    assert not r.abstained and r.abstention_reason is None and r.abstention_note == ""
    assert [x.weak for x in r.results] == [False, True]
    assert r.support_status == "supported" and r.support_best == 0.9


def test_the_gate_decides_and_the_check_verdict_is_kept_honestly() -> None:
    # The answer check judged insufficient; its vote was counted and the
    # memory still clears the gate: the answer is shown, and the check's own
    # word stays "unsupported" for any caller that gates on it.
    r = finalize_score_contract(_response("a"), verdict=_verdict(True),
                                support=_support(0.9))
    assert r.results and not r.abstained
    assert r.answer_check_insufficient is True
    r.answer_check_status = "judged"
    assert ans.of_response(r)[0] == ans.UNSUPPORTED


def test_check_and_gate_agreeing_on_no_answer_is_reported_as_the_check() -> None:
    r = finalize_score_contract(_response("a"), verdict=_verdict(True),
                                support=_support(0.1))
    assert r.results == [] and r.abstention_reason == "judged_insufficient"
    assert r.abstention_note == sg.ABSTAIN_NOTE


def test_without_a_support_verdict_the_contract_is_unchanged() -> None:
    r = finalize_score_contract(_response("a"), verdict=_verdict(True))
    assert r.results and r.abstained and r.abstention_reason == "judged_insufficient"
    assert r.support_status == "" and r.abstention_note == ""


def test_the_envelope_carries_the_reason_and_each_result_its_support() -> None:
    from superlocalmemory.server.recall_serializer import (
        recall_response_metadata,
        serialize_recall_response,
    )

    r = finalize_score_contract(_response("a"), support=_support(0.2), show_weak=True)
    meta = recall_response_metadata(r)
    assert meta["abstention_note"].startswith(sg.ABSTAIN_NOTE)
    assert meta["support"] == {"status": "unsupported", "best": 0.2, "threshold": 0.5,
                               "version": "test", "weak_shown": True, "tier": "none"}
    items, _ = serialize_recall_response(r, limit=5)
    assert items[0]["weak"] is True and items[0]["support"] == 0.2
    empty = finalize_score_contract(_response("a"), support=_support(0.2))
    assert recall_response_metadata(empty)["answerability"] == ans.UNSUPPORTED
    envelope = {**recall_response_metadata(empty), "results": [], "result_count": 0}
    assert ans.of_envelope(envelope) == (ans.UNSUPPORTED, ans.REASON_NO_SUPPORT)


def test_an_empty_recall_says_why_in_plain_words() -> None:
    floored = RecallResponse(query="q", no_confident_match=True)
    r = finalize_score_contract(floored)
    assert r.abstention_reason == "evidence_floor" and r.abstention_note == sg.ABSTAIN_NOTE
    r = finalize_score_contract(RecallResponse(query="q"))
    assert r.abstention_reason == "no_candidates"
    assert r.abstention_note == "Nothing in memory matched this question."
    assert ans.of_response(r) == (ans.UNJUDGED, ans.REASON_NO_RESULTS)


def test_run_recall_hard_abstains_and_show_weak_reaches_the_contract(monkeypatch) -> None:
    from superlocalmemory.core import recall_pipeline
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    weak = {"hub_corrected": -0.2, "rare_miss": 1.0, "ce_raw": -11.0, "cosine": 0.4}

    def recall(*_a, **_kw):
        out = _response("a", "b")
        for x in out.results:
            x.evidence = dict(weak)
        out.query_evidence = {"latin_script": True, "nonsense_similarity": 0.45}
        return out

    engine = SimpleNamespace(_sufficiency_judge=None, recall=recall)
    monkeypatch.setattr(recall_pipeline, "apply_ranking", lambda resp, *a, **k: resp)
    # A stand-in store has no sources to check; the gate is what is under test.
    from superlocalmemory.retrieval import source_fidelity_flags
    monkeypatch.setattr(source_fidelity_flags, "flag_unverified",
                        lambda results, *a, **k: results)
    config = SLMConfig.for_mode(Mode.A)
    kwargs = dict(fast=True, config=config, retrieval_engine=engine, trust_scorer=None,
                  embedder=None, db=SimpleNamespace(db_path=None), llm=None, hooks=None)
    plain = recall_pipeline.run_recall("zxqv plorth", "default", **kwargs)
    assert plain.results == [] and plain.abstention_reason == "unsupported"
    shown = recall_pipeline.run_recall("zxqv plorth", "default", show_weak=True, **kwargs)
    assert [x.fact.fact_id for x in shown.results] == ["a", "b"] and shown.weak_shown


def test_a_hard_abstain_keeps_what_the_answer_check_said() -> None:
    r = finalize_score_contract(_response("a"), verdict=_verdict(True), support=_support(0.1))
    assert r.results == []
    assert r.calibration_status == "measured" and r.answer_confidence == 0.3
    assert r.calibration_id == "laya@x"
