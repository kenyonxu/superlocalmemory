# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The answer-support gate: one readable model over every signal, per memory."""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from superlocalmemory.retrieval import support_gate as sg
from superlocalmemory.retrieval import support_recipe as sr

#: A hand-written recipe so these tests check the mechanics, not the fit.
RECIPE = sr.SupportRecipe(
    version="test-recipe",
    weights={"hub_corrected": 10.0, "keyword_coverage": 2.0, "cross_encoder": 0.2,
             "log_bm25": 0.1, "entity_hit": 0.5, "oov_phrase": -1.0, "rare_miss": -5.0,
             "nonsense_similarity": -20.0, "orphan_terms": -3.0, "unframed_miss": -4.0},
    intercept=3.0, threshold=0.5, ce_clip=(-12.0, 10.0), ce_missing=-4.0,
    hub_missing_baseline=0.6, hub_corrected_unmeasured=0.12, nonsense_similarity_missing=0.40,
    laya_unsupported=-2.0, metadata_bonus=0.5, fitted_on="hand-written",
)
QE = {"latin_script": True, "oov_phrase": False, "nonsense_similarity": 0.33}


def _ev(**kw):
    base = {"cosine": 0.7, "hub_corrected": 0.25, "keyword_coverage": 0.5, "ce_raw": 4.0,
            "bm25_raw": 6.0, "entity_hit": False, "rare_miss": 0.0}
    return {**base, **kw}


def _result(evidence, *, pinned=False, fid="f1"):
    fact = SimpleNamespace(fact_id=fid, pinned=pinned)
    return SimpleNamespace(fact=fact, evidence=evidence)


def test_features_are_the_recipe_inputs_with_imputation_flags() -> None:
    f = sg.features(_ev(), QE, RECIPE)
    assert f["hub_corrected"] == pytest.approx(0.25)
    assert f["cross_encoder"] == pytest.approx(4.0)
    assert f["log_bm25"] == pytest.approx(math.log1p(6.0))
    assert f["nonsense_similarity"] == pytest.approx(0.33)
    assert set(RECIPE.weights) <= set(f)
    assert sg.imputed(_ev(), QE) == ()


def test_cross_encoder_votes_only_on_latin_script_and_is_clipped() -> None:
    assert sg.features(_ev(ce_raw=50.0), QE, RECIPE)["cross_encoder"] == 10.0
    non_latin = {**QE, "latin_script": False}
    assert sg.features(_ev(ce_raw=9.0), non_latin, RECIPE)["cross_encoder"] == -4.0
    assert sg.features(_ev(ce_raw=None), QE, RECIPE)["cross_encoder"] == -4.0
    assert "cross_encoder" in sg.imputed(_ev(ce_raw=None), QE)


def test_missing_hub_baseline_and_query_profile_are_imputed_conservatively() -> None:
    f = sg.features(_ev(hub_corrected=None, cosine=0.8), {**QE, "nonsense_similarity": None},
                    RECIPE)
    assert f["hub_corrected"] == pytest.approx(0.8 - 0.6)
    assert f["nonsense_similarity"] == pytest.approx(0.40)
    # No vector at all (a keyword-only recall, the model not loaded): neither
    # rewarded nor written off -- the recipe's own value for "unmeasured".
    no_vector = sg.features(_ev(hub_corrected=None, cosine=None), QE, RECIPE)
    assert no_vector["hub_corrected"] == pytest.approx(RECIPE.hub_corrected_unmeasured)
    assert set(sg.imputed(_ev(hub_corrected=None), {})) >= {"hub_corrected",
                                                             "nonsense_similarity"}


def test_probability_is_a_logistic_of_the_weighted_sum() -> None:
    f = sg.features(_ev(), QE, RECIPE)
    z = RECIPE.intercept + sum(RECIPE.weights[k] * f[k] for k in RECIPE.weights)
    assert sg.probability(_ev(), QE, RECIPE) == pytest.approx(1 / (1 + math.exp(-z)))


def test_one_known_token_plus_gibberish_loses_to_the_bare_token() -> None:
    bare = sg.probability(_ev(keyword_coverage=1.0, rare_miss=0.0), QE, RECIPE)
    padded = sg.probability(_ev(keyword_coverage=0.5, rare_miss=0.5), QE, RECIPE)
    assert bare > RECIPE.threshold > padded


def test_assess_answers_when_any_memory_clears_the_threshold() -> None:
    results = [_result(_ev(hub_corrected=-0.2, rare_miss=1.0), fid="a"),
               _result(_ev(), fid="b")]
    a = sg.assess(results, QE, recipe=RECIPE)
    assert a.supported and a.best == pytest.approx(max(a.probabilities))
    assert a.probabilities[0] < RECIPE.threshold < a.probabilities[1]
    assert a.version == "test-recipe" and a.threshold == RECIPE.threshold


def test_assess_abstains_when_nothing_clears_and_on_no_results() -> None:
    weak = [_result(_ev(hub_corrected=-0.1, rare_miss=1.0, ce_raw=-11.0))]
    a = sg.assess(weak, QE, recipe=RECIPE)
    assert not a.supported and a.reason == sg.REASON_UNSUPPORTED
    empty = sg.assess([], QE, recipe=RECIPE)
    assert not empty.supported and empty.best is None


def test_a_result_without_evidence_is_never_support() -> None:
    a = sg.assess([_result({})], QE, recipe=RECIPE)
    assert not a.supported and a.probabilities == (0.0,)


def test_laya_unsupported_lowers_support_and_supported_never_raises_it() -> None:
    results = [_result(_ev())]
    plain = sg.assess(results, QE, recipe=RECIPE).best
    no = SimpleNamespace(insufficient=True)
    yes = SimpleNamespace(insufficient=False)
    assert sg.assess(results, QE, verdict=no, recipe=RECIPE).best < plain
    assert sg.assess(results, QE, verdict=yes, recipe=RECIPE).best == pytest.approx(plain)


def test_metadata_agreement_is_a_bonus_only_without_missing_rare_terms() -> None:
    base = sg.probability(_ev(), QE, RECIPE)
    assert sg.probability(_ev(metadata_hit=True), QE, RECIPE) > base
    missing = sg.probability(_ev(rare_miss=1.0), QE, RECIPE)
    assert sg.probability(_ev(rare_miss=1.0, metadata_hit=True), QE, RECIPE) == \
        pytest.approx(missing)


def test_active_recipe_is_versioned_complete_and_deterministic() -> None:
    r = sr.ACTIVE
    assert r.version.startswith("answer-support-")
    assert set(r.weights) == set(sr.FEATURES)
    assert 0.0 < r.threshold < 1.0
    assert sr.recipe(r.version) is r
    with pytest.raises(KeyError):
        sr.recipe("answer-support-v0")
    assert sg.probability(_ev(), QE) == sg.probability(_ev(), QE)


def test_recipe_rejects_unknown_features() -> None:
    with pytest.raises(ValueError):
        replace(RECIPE, weights={**RECIPE.weights, "mystery": 1.0})


def test_lexical_support_is_the_gate_when_only_words_are_left() -> None:
    """The keyword fallback (a recall past its budget) has no vectors and no
    cross-encoder; a memory counts only when it carries every rare term of the
    question and at least half its content words."""
    note = "Hub v0.3.4 fixed the resultType bug when a tool returned no content."
    assert sg.lexical_support("v0.3.4", note)
    assert sg.lexical_support("What did hub v0.3.4 fix?", note)
    assert not sg.lexical_support("v0.3.4 snazzle", note)
    assert not sg.lexical_support("purple elephants juggling quantum content", note)
    assert not sg.lexical_support("", note)


def test_the_keyword_fallback_drops_what_the_words_do_not_support() -> None:
    from superlocalmemory.server import recall_fallback

    rows = [{"fact_id": "a", "content": "Hub v0.3.4 fixed the resultType bug.", "confidence": 1},
            {"fact_id": "b", "content": "v0.3.4 build passed", "confidence": 1}]
    kept = recall_fallback.supported_rows("What did hub v0.3.4 fix?", rows)
    assert [r["fact_id"] for r in kept] == ["a"]
    assert recall_fallback.supported_rows("v0.3.4 snazzle", rows) == []


def test_orphan_words_lower_support_and_unmeasured_is_neutral() -> None:
    base = sg.probability(_ev(), QE, RECIPE)
    assert sg.probability(_ev(), {**QE, "orphan_terms": 1 / 3}, RECIPE) < base
    assert sg.features(_ev(), {**QE, "orphan_terms": None}, RECIPE)["orphan_terms"] == 0.0


def test_a_keyword_query_must_find_its_words_in_the_memory() -> None:
    """No question word: the words ARE the question, so the ones the memory
    lacks count against it. With a question word, they may be the thing asked."""
    unframed = {**QE, "question_frame": False}
    framed = {**QE, "question_frame": True}
    assert sg.features(_ev(subject_coverage=0.5), unframed, RECIPE)["unframed_miss"] == 0.5
    assert sg.features(_ev(subject_coverage=0.5), framed, RECIPE)["unframed_miss"] == 0.0
    assert sg.features(_ev(), unframed, RECIPE)["unframed_miss"] == 0.0  # unmeasured
    assert sg.probability(_ev(subject_coverage=0.5), unframed, RECIPE) < \
        sg.probability(_ev(subject_coverage=0.5), framed, RECIPE)
