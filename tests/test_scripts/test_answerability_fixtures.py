# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The answerability harness measures what it says it measures.

Fast checks on the corpus and question sets only; the measurement itself is
``tests/test_retrieval/test_answerability_gate.py`` (slow, real models).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

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
def corpus():
    return _load("answerability_corpus")


def test_store_is_about_two_thousand_rows_and_mostly_id_heavy(corpus) -> None:
    rows = corpus.corpus()
    assert 1900 <= len(rows) <= 2100
    hub_kinds = ("receipt", "log", "session", "commit", "tool_event", "stub")
    hubs = [r for r in rows if r.kind in hub_kinds]
    assert len(hubs) > len(rows) / 2
    assert any(re.search(r"asdk_app_[0-9a-f]{16}", r.content) for r in rows)
    assert any(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", r.content) for r in rows)


def test_store_is_unevenly_described_like_a_year_old_one(corpus) -> None:
    rows = corpus.corpus()
    legacy = [r for r in rows if r.slice == corpus.LEGACY]
    assert 0.35 <= len(legacy) / len(rows) <= 0.45
    assert all(r.metadata is None and r.stored_date is None for r in legacy)
    described = [r for r in rows if r.slice == corpus.DESCRIBED]
    assert all(r.metadata and r.stored_date for r in described)
    tiny = sum(len(r.content) < 40 for r in rows) / len(rows)
    assert 0.20 <= tiny <= 0.28
    assert {r.lifecycle for r in rows} == {"active", "warm", "cold", "archived"}
    keys = {r.key: r for r in rows}
    real = corpus.load_questions("real")
    assert all(keys[k].lifecycle != "archived" for q in real for k in q["expected"])
    by_slice = {keys[q["expected"][0]].slice for q in real}
    assert by_slice == {corpus.LEGACY, corpus.DESCRIBED}


def test_generation_is_deterministic(corpus) -> None:
    assert corpus.corpus() == corpus.corpus()
    # Pinned: a change to the generator or the anchors must bump CORPUS_VERSION
    # and re-record the baseline it was measured against.
    assert corpus.digest() == "d21fd3d53039f6e6"


def test_question_sets_meet_the_plan_sizes_and_point_at_real_rows(corpus) -> None:
    keys = {r.key for r in corpus.corpus()}
    nonsense = corpus.load_questions("nonsense")
    real = corpus.load_questions("real")
    unanswerable = corpus.load_questions("unanswerable")
    assert len(nonsense) >= 60 and len(real) >= 60
    assert all(q["expected"] == "abstain" for q in nonsense + unanswerable)
    for q in real:
        assert q["expected"] and set(q["expected"]) <= keys, q["qid"]
    qids = [q["qid"] for q in nonsense + real + unanswerable]
    assert len(qids) == len(set(qids))
    categories = {q["category"] for q in nonsense}
    assert {"random_letters", "in_vocabulary_nonsense", "id_like_unstored",
            "shares_one_token"} <= categories
    real_categories = {q["category"] for q in real}
    assert {"code_identifier", "version", "acronym", "exact_id", "id_note_paraphrase",
            "name", "non_english", "semantic_only", "paraphrase_pair"} <= real_categories


def test_no_nonsense_question_or_unstored_id_appears_in_the_store(corpus) -> None:
    text = "\n".join(r.content for r in corpus.corpus()).lower()
    for q in corpus.load_questions("nonsense"):
        assert q["question"].lower() not in text, q["qid"]


def test_auc_and_gate_helpers() -> None:
    ev = _load("answerability_eval")
    assert ev.auc([1.0, 2.0], [0.0]) == 1.0
    assert ev.auc([0.0], [1.0]) == 0.0
    assert ev.auc([1.0], [1.0]) == 0.5
    assert ev.auc([], [1.0]) is None
    ok = {"profiles": [{"profile": "a", "nonsense_abstain_rate": 1.0, "real_top5_hit_rate": 1.0}]}
    assert ev.gate_failures(ok) == []
    bad = {"profiles": [{"profile": "a", "nonsense_abstain_rate": 0.5, "real_top5_hit_rate": 1.0}]}
    assert ev.gate_failures(bad) == ["a: nonsense abstain 0.5"]


def test_refuses_the_owners_own_store(tmp_path) -> None:
    ev = _load("answerability_eval")
    import os
    import pwd

    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    with pytest.raises(ev.Refused):
        ev._refuse_own_store(home / ".superlocalmemory" / "x")
    ev._refuse_own_store(tmp_path)  # a scratch folder is fine
