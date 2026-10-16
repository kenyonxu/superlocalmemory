# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The merge rule's measurement: every condition, gated against gate-off.

Fast checks on the pieces; the run itself is the harness with ``--conditions``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "quality" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cond():
    return _load("answerability_conditions")


def _row(qid, kind, rank=None, n=1):
    return {"qid": qid, "set": kind, "rank": rank, "result_count": n, "abstained": n == 0}


def test_a_real_answer_is_lost_when_gate_off_had_it_in_the_top_five_and_gated_not(cond):
    gate_off = [_row("r1", "real", 1), _row("r2", "real", 3), _row("r3", "real", None),
                _row("n1", "nonsense", None, 2)]
    gated = [_row("r1", "real", 1), _row("r2", "real", None, 0), _row("r3", "real", 2),
             _row("n1", "nonsense", None, 0)]
    out = cond.compare(gated, gate_off)
    assert out["real_lost"] == ["r2"] and out["real_gained"] == ["r3"]
    assert out["gated"]["real_hits"] == 2 and out["gate_off"]["real_hits"] == 2
    assert out["gated"]["nonsense_abstain_rate"] == 1.0
    assert out["gate_off"]["nonsense_abstain_rate"] == 0.0


def test_conditions_patch_the_engine_and_put_everything_back(cond, monkeypatch):
    from superlocalmemory.retrieval import hub_baseline

    re = SimpleNamespace(_reranker="ce", _query_embedding_cache={"q": [1.0]},
                         _probe_matrix=lambda: "probes", _hub_baselines=lambda *a: {"f": 1},
                         _embed_query=lambda q: ([1.0], None))
    engine = SimpleNamespace(_retrieval_engine=re)
    lazy = hub_baseline.lazy_warm
    with cond.condition(engine, "keyword_only"):
        assert re._reranker is None and re._probe_matrix() is None
        assert re._embed_query("q") == (None, None) and re._query_embedding_cache == {}
        assert re._hub_baselines([], "p") == {} and hub_baseline.lazy_warm(None) is False
    assert re._reranker == "ce" and re._probe_matrix() == "probes"
    assert re._query_embedding_cache == {"q": [1.0]} and hub_baseline.lazy_warm is lazy
    with cond.condition(engine, "ce_absent"):
        assert re._reranker is None and re._probe_matrix() == "probes"
    with cond.condition(engine, "probes_cold"):
        assert re._reranker == "ce" and re._probe_matrix() is None
        assert re._hub_baselines([], "p") == {}
    with pytest.raises(ValueError):
        with cond.condition(engine, "nope"):
            pass


def test_gate_off_sets_and_clears_the_switch(cond):
    os.environ.pop("SLM_RECALL_NO_SUPPORT_GATE", None)
    with cond.gate_off():
        assert os.environ["SLM_RECALL_NO_SUPPORT_GATE"] == "1"
    assert "SLM_RECALL_NO_SUPPORT_GATE" not in os.environ


def test_each_question_is_asked_gate_off_then_gated_back_to_back(monkeypatch):
    """Whole-suite passes drift (every recall reinforces what it returns), so a
    memory at rank five in one pass sat at six in the next and read as a loss."""
    ev = _load("answerability_eval")
    questions = [("real", {"qid": f"r{i}", "question": f"q{i}", "expected": ["k"]})
                 for i in range(3)]
    monkeypatch.setattr(ev, "_questions", lambda suite: questions)
    seen = []

    class _Engine:
        _retrieval_engine = SimpleNamespace()

        def recall(self, question, **kw):
            seen.append((question, os.environ.get("SLM_RECALL_NO_SUPPORT_GATE") == "1"))
            return SimpleNamespace(results=[], support_tier="none", warming_up=False)

    out = ev.measure_conditions(_Engine(), "frozen", {}, ["warm"], log=lambda m: None)
    assert seen == [(f"q{i}", off) for i in range(3) for off in (True, False)]
    assert out["warm"]["gated"]["real_n"] == 3 and out["warm"]["real_lost"] == []
