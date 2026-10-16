# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Three tiers: supported, related (a real question, close memories), none."""

from __future__ import annotations

from types import SimpleNamespace

from superlocalmemory.core.score_contract import finalize_score_contract
from superlocalmemory.retrieval import support_gate as sg
from superlocalmemory.retrieval import support_tiers as st
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult

REAL_Q = {"oov_phrase": False, "orphan_terms": 0.25, "nonsense_similarity": 0.33}
NONSENSE_Q = {"oov_phrase": False, "orphan_terms": 0.67, "nonsense_similarity": 0.33}


def _r(fid, evidence, content="x" * 600):
    r = RetrievalResult(fact=AtomicFact(fact_id=fid, memory_id=f"m-{fid}", content=content))
    r.evidence = dict(evidence)
    return r


CLOSE = {"hub_corrected": 0.2, "subject_coverage": 0.3, "rare_miss": 0.0}
FAR = {"hub_corrected": 0.02, "subject_coverage": 0.0, "rare_miss": 1.0}


def test_a_question_reads_as_nonsense_on_gibberish_orphans_or_its_profile() -> None:
    assert not st.question_is_nonsense(REAL_Q)
    assert st.question_is_nonsense(NONSENSE_Q)
    assert st.question_is_nonsense({**REAL_Q, "oov_phrase": True})
    assert st.question_is_nonsense({**REAL_Q, "nonsense_similarity": 0.45})
    assert not st.question_is_nonsense({})  # nothing measured: not called nonsense


def test_real_evidence_is_a_standout_cosine_or_the_rare_terms_and_half_the_words() -> None:
    assert st.real_evidence(CLOSE)
    assert st.real_evidence({"hub_corrected": 0.0, "subject_coverage": 0.5, "rare_miss": 0.0})
    assert not st.real_evidence(FAR)
    assert not st.real_evidence({"hub_corrected": 0.0, "subject_coverage": 0.5, "rare_miss": 0.5})


def test_related_takes_at_most_three_close_memories_best_first() -> None:
    results = [_r(str(i), CLOSE) for i in range(5)] + [_r("far", FAR)]
    probs = (0.1, 0.25, 0.2, 0.05, 0.15, 0.27)
    assert st.related_indexes(results, probs, REAL_Q) == [1, 2, 4]
    assert st.related_indexes(results, probs, NONSENSE_Q) == []
    assert st.related_indexes([_r("far", FAR)], (0.2,), REAL_Q) == []


def _support(*probs, threshold=0.5):
    best = max(probs) if probs else None
    ok = best is not None and best >= threshold
    return sg.Assessment(supported=ok, best=best, threshold=threshold, probabilities=probs,
                         version="t", reason=None if ok else sg.REASON_UNSUPPORTED)


def _response(qe, *results):
    return RecallResponse(query="q", results=list(results), query_evidence=dict(qe))


def test_nonsense_is_tier_none_with_the_gentle_reason() -> None:
    r = finalize_score_contract(_response(NONSENSE_Q, _r("a", CLOSE)), support=_support(0.2),
                                include_related=True)
    assert r.results == [] and r.support_tier == st.NONE
    assert r.abstention_note == st.NONE_NOTE == "That isn't in SuperLocalMemory."


def test_related_is_shown_only_to_callers_that_ask_and_labelled() -> None:
    rs = [_r(str(i), CLOSE) for i in range(4)]
    shown = finalize_score_contract(_response(REAL_Q, *rs), support=_support(.2, .3, .1, .25),
                                    include_related=True)
    assert shown.support_tier == st.RELATED
    assert [x.fact.fact_id for x in shown.results] == ["1", "3", "0"]
    assert all(x.tier == st.RELATED and x.weak for x in shown.results)
    assert shown.abstention_note == st.RELATED_NOTE and shown.abstained
    hidden = finalize_score_contract(_response(REAL_Q, *[_r(str(i), CLOSE) for i in range(4)]),
                                     support=_support(.2, .3, .1, .25))
    assert hidden.results == [] and hidden.support_tier == st.RELATED
    assert hidden.abstention_note == st.RELATED_HIDDEN_NOTE


def test_supported_results_are_labelled_supported_or_weak() -> None:
    r = finalize_score_contract(_response(REAL_Q, _r("a", CLOSE), _r("b", FAR)),
                                support=_support(0.9, 0.1))
    assert r.support_tier == st.SUPPORTED
    assert [x.tier for x in r.results] == [st.SUPPORTED, "weak"]


def test_show_weak_keeps_everything_labelled_with_the_tier_note() -> None:
    r = finalize_score_contract(_response(NONSENSE_Q, _r("a", CLOSE)), support=_support(0.2),
                                show_weak=True)
    assert len(r.results) == 1 and r.results[0].tier == "weak"
    assert r.abstention_note.startswith(st.NONE_NOTE) and r.weak_shown


def test_the_envelope_carries_the_tier_and_short_previews_for_related() -> None:
    from superlocalmemory.server.recall_serializer import (
        recall_response_metadata,
        serialize_recall_response,
    )

    r = finalize_score_contract(_response(REAL_Q, _r("a", CLOSE)), support=_support(0.2),
                                include_related=True)
    meta = recall_response_metadata(r)
    assert meta["tier"] == st.RELATED and meta["support"]["tier"] == st.RELATED
    items, _ = serialize_recall_response(r, limit=5)
    assert items[0]["tier"] == st.RELATED
    assert len(items[0]["content"]) <= st.PREVIEW_CHARS + 1
    assert SimpleNamespace  # keep the import used for future fakes


COLD_Q = {"oov_phrase": False, "orphan_terms": 0.67, "nonsense_similarity": None}
NO_VECTOR = {"hub_corrected": None, "cosine": None, "subject_coverage": 0.5, "rare_miss": 0.0}


def _support_cold(*probs, threshold=0.5):
    base = _support(*probs, threshold=threshold)
    from dataclasses import replace
    return replace(base, degraded=True)


def test_while_warming_up_keyword_finds_show_as_related_to_every_caller() -> None:
    rs = [_r(str(i), NO_VECTOR) for i in range(7)]
    r = finalize_score_contract(_response(COLD_Q, *rs), support=_support_cold(*[0.1] * 7))
    assert r.warming_up and r.status_note == st.WARMING_NOTE
    # The recall's own limit: a find at rank six is not dropped while warming.
    assert r.support_tier == st.RELATED and len(r.results) == 7
    assert all(x.tier == st.RELATED for x in r.results)
    assert r.abstention_note == st.WARMING_NOTE
    assert "isn't in SuperLocalMemory" not in r.abstention_note


def test_while_warming_up_an_empty_recall_says_warming_never_not_in_memory() -> None:
    empty = RecallResponse(query="q", query_evidence={"nonsense_similarity": None},
                           channel_status={"semantic": "warming"})
    r = finalize_score_contract(empty)
    assert r.warming_up and r.abstention_note == st.WARMING_NOTE
    warm_empty = finalize_score_contract(RecallResponse(
        query="q", no_confident_match=True,
        query_evidence={"nonsense_similarity": 0.3}))
    assert not warm_empty.warming_up and warm_empty.abstention_note == st.NONE_NOTE


def test_symbols_and_emoji_alone_are_nonsense_even_while_warming() -> None:
    assert st.symbol_only("\U0001F600\U0001F680 !!")
    assert not st.symbol_only("v0.3.4")
    rs = [_r("a", NO_VECTOR)]
    r = finalize_score_contract(RecallResponse(query="\U0001F600\U0001F680", results=rs,
                                               query_evidence={}),
                                support=_support_cold(0.1))
    assert r.results == [] and r.support_tier == st.NONE
    assert r.abstention_note == st.NONE_NOTE


def test_the_envelope_says_warming_up() -> None:
    from superlocalmemory.server.recall_serializer import recall_response_metadata

    r = finalize_score_contract(_response(COLD_Q, _r("a", NO_VECTOR)), support=_support_cold(0.1))
    meta = recall_response_metadata(r)
    assert meta["warming_up"] is True and meta["status_note"] == st.WARMING_NOTE


def test_assess_marks_a_recall_without_measurements_as_degraded() -> None:
    qe = {"latin_script": True, "nonsense_similarity": None}
    a = sg.assess([_r("a", NO_VECTOR)], qe)
    assert a.degraded
    warm = sg.assess([_r("a", {**CLOSE, "cosine": 0.6, "ce_raw": 1.0})],
                     {**qe, "nonsense_similarity": 0.3})
    assert not warm.degraded


WARM_NO_CE = {"hub_corrected": 0.02, "cosine": 0.5, "subject_coverage": 0.0, "rare_miss": 1.0}


def test_assess_marks_a_latin_recall_without_any_cross_encoder_score_as_degraded() -> None:
    qe = {"latin_script": True, "nonsense_similarity": 0.3}
    assert sg.assess([_r("a", WARM_NO_CE), _r("b", WARM_NO_CE)], qe).degraded
    scored = sg.assess([_r("a", {**WARM_NO_CE, "ce_raw": -3.0}), _r("b", WARM_NO_CE)], qe)
    assert not scored.degraded
    # Non-Latin questions never use the cross-encoder: its absence is not a gap.
    assert not sg.assess([_r("a", WARM_NO_CE)], {**qe, "latin_script": False}).degraded


def test_without_a_cross_encoder_finds_are_kept_labelled_and_not_called_warming() -> None:
    rs = [_r(str(i), WARM_NO_CE) for i in range(7)]
    response = _response({"nonsense_similarity": 0.3, "latin_script": True}, *rs)
    response.reranker_status = "not_configured"
    r = finalize_score_contract(response, support=_support_cold(*[0.1] * 7))
    assert len(r.results) == 7 and all(x.tier == st.RELATED for x in r.results)
    assert not r.warming_up and r.status_note == ""
    assert r.abstention_note == st.RELATED_NOTE


def test_a_cross_encoder_still_loading_is_warming_up() -> None:
    response = _response({"nonsense_similarity": 0.3, "latin_script": True},
                         _r("a", WARM_NO_CE))
    response.reranker_status = "fallback_not_ready"
    r = finalize_score_contract(response, support=_support_cold(0.1))
    assert r.warming_up and r.abstention_note == st.WARMING_NOTE


def test_cross_encoder_absence_is_read_from_the_reranker_status_when_known() -> None:
    qe = {"latin_script": True, "nonsense_similarity": 0.3}
    scored = [_r("a", {**WARM_NO_CE, "ce_raw": 2.0})]
    unscored = [_r("a", WARM_NO_CE)]
    # The reranker did not run: degraded, even if a stale score is present.
    assert sg.assess(scored, qe, reranker_status="not_configured").degraded
    assert sg.assess(scored, qe, reranker_status="fallback_not_ready").degraded
    # It ran: a result outside its window carries no score, which is no gap.
    assert not sg.assess(unscored, qe, reranker_status="applied").degraded
    # Unknown status (a stand-in engine): fall back to the scores.
    assert sg.assess(unscored, qe).degraded and not sg.assess(scored, qe).degraded


def test_the_support_stage_passes_the_reranker_status_through() -> None:
    from superlocalmemory.core import support_stage

    class _Engine:
        _db = None

        def _attach_evidence(self, *a):
            return {}

    response = _response({"latin_script": True, "nonsense_similarity": 0.3},
                         _r("a", WARM_NO_CE))
    response.reranker_status = "applied"
    assert not support_stage.assess(_Engine(), "q", response, None, "default").degraded
    response.reranker_status = "not_configured"
    response.results = [_r("a", {**WARM_NO_CE, "ce_raw": 2.0})]
    assert support_stage.assess(_Engine(), "q", response, None, "default").degraded
