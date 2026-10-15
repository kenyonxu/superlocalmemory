# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The recall pipeline's support stage: evidence for every result, then the gate."""

from __future__ import annotations

from types import SimpleNamespace

from superlocalmemory.core import support_stage
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult


def _result(fid: str, evidence: dict | None = None) -> RetrievalResult:
    r = RetrievalResult(fact=AtomicFact(fact_id=fid, memory_id=f"m-{fid}", content="x"))
    r.evidence = dict(evidence or {})
    return r


class _Engine:
    def __init__(self):
        self.asked: list[list[str]] = []

    def _attach_evidence(self, query, results, ce_scores, profile_id):
        self.asked.append([r.fact.fact_id for r in results])
        for r in results:
            r.evidence = {"hub_corrected": -0.3, "rare_miss": 1.0}
        return {"latin_script": True, "nonsense_similarity": 0.45}


def test_results_rebuilt_without_evidence_get_it_before_the_gate() -> None:
    engine = _Engine()
    response = RecallResponse(query="q", results=[_result("a", {"hub_corrected": 0.4}),
                                                  _result("b")])
    support = support_stage.assess(engine, "q", response, None, "default")
    assert engine.asked == [["b"]]
    assert response.query_evidence["nonsense_similarity"] == 0.45
    assert len(support.probabilities) == 2


def test_nothing_to_judge_is_no_verdict() -> None:
    # No results: nothing was found to judge (a channel may have been down), so
    # the empty set keeps its own reason. No evidence at all (an engine
    # stand-in that cannot measure): the gate does not run.
    engine = _Engine()
    assert support_stage.assess(engine, "q", RecallResponse(query="q"), None, "default") is None
    assert engine.asked == []
    bare = RecallResponse(query="q", results=[_result("a")])
    assert support_stage.assess(SimpleNamespace(), "q", bare, None, "default") is None


def test_explicit_filters_are_facets_windows_and_time_travel() -> None:
    assert not support_stage.explicit_filter(None, None)
    assert not support_stage.explicit_filter(SimpleNamespace(narrows=False), None)
    assert support_stage.explicit_filter(SimpleNamespace(narrows=True), None)
    assert support_stage.explicit_filter(None, "7d")
    assert support_stage.explicit_filter(None, None, valid_at="2026-01-01")


def test_metadata_agreement_marks_memories_whose_tag_or_project_the_question_names() -> None:
    rows = {"a": {"tags": '["release", "hub"]', "project": "Core"},
            "b": {"tags": None, "project": None}}
    results = [_result("a", {"rare_miss": 0.0}), _result("b", {"rare_miss": 0.0})]
    support_stage.mark_metadata(results, "What did the hub release fix?",
                                lambda ids: {k: rows[k] for k in ids})
    assert results[0].evidence["metadata_hit"] is True
    assert results[1].evidence["metadata_hit"] is False
    support_stage.mark_metadata(results, "anything", lambda ids: (_ for _ in ()).throw(
        RuntimeError("db down")))  # fail-open


def test_the_gate_switches_off_by_config_or_env(monkeypatch) -> None:
    on = SimpleNamespace(retrieval=SimpleNamespace(answer_support_gate=True))
    off = SimpleNamespace(retrieval=SimpleNamespace(answer_support_gate=False))
    assert support_stage.enabled(on) and not support_stage.enabled(off)
    assert support_stage.enabled(SimpleNamespace())
    monkeypatch.setenv("SLM_RECALL_NO_SUPPORT_GATE", "1")
    assert not support_stage.enabled(on)


def test_the_kill_switch_reads_true_and_yes_and_also_turns_off_the_floor_door(monkeypatch) -> None:
    from superlocalmemory.retrieval import support_gate as sg
    from superlocalmemory.retrieval.engine import RetrievalEngine

    on = SimpleNamespace(retrieval=SimpleNamespace(answer_support_gate=True))
    for value in ("1", "true", "YES", "True"):
        monkeypatch.setenv("SLM_RECALL_NO_SUPPORT_GATE", value)
        assert not support_stage.enabled(on)
        assert sg.switched_off(SimpleNamespace(answer_support_gate=True))
    monkeypatch.setenv("SLM_RECALL_NO_SUPPORT_GATE", "0")
    assert support_stage.enabled(on)
    assert sg.switched_off(SimpleNamespace(answer_support_gate=False))
    eng = RetrievalEngine.__new__(RetrievalEngine)
    eng._config = SimpleNamespace(answer_support_gate=False)
    eng._probe_matrix = lambda: (_ for _ in ()).throw(AssertionError("measured"))
    assert eng._floor_hub_corrected([], {}, 0.6, "default") == {}


def test_prefer_project_is_not_an_explicit_filter() -> None:
    from superlocalmemory.retrieval.facets import Facets

    assert not support_stage.explicit_filter(Facets.of(prefer_project="core"), None)
    assert support_stage.explicit_filter(Facets.of(project="core"), None)


def test_a_small_store_does_not_penalise_orphan_or_rare_words(monkeypatch) -> None:
    monkeypatch.setattr(support_stage, "_SIZE_CACHE", {})
    rows = {"n": 40}
    db = SimpleNamespace(execute=lambda sql, params=(): [{"n": rows["n"]}])
    engine = SimpleNamespace(_db=db, _attach_evidence=None)
    response = RecallResponse(query="postgres version",
                              results=[_result("a", {"rare_miss": 1.0, "hub_corrected": 0.1})])
    response.query_evidence = {"orphan_terms": 0.5, "nonsense_similarity": 0.3}
    support_stage.assess(engine, "postgres version", response, None, "default")
    assert response.query_evidence["orphan_terms"] is None
    assert response.query_evidence["small_store"] is True
    assert response.results[0].evidence["rare_miss"] == 0.0
    monkeypatch.setattr(support_stage, "_SIZE_CACHE", {})
    rows["n"] = 5000
    big = RecallResponse(query="q", results=[_result("a", {"rare_miss": 1.0})])
    big.query_evidence = {"orphan_terms": 0.5}
    support_stage.assess(engine, "q", big, None, "default")
    assert big.query_evidence["orphan_terms"] == 0.5 and big.results[0].evidence["rare_miss"] == 1.0
