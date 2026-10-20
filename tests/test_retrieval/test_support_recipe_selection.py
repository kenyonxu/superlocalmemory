# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Which recipe the gate decides with: config first, model key second."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from superlocalmemory.core import support_stage
from superlocalmemory.retrieval import support_recipe as sr

NOMIC = "nomic-ai/nomic-embed-text-v1.5"


def _engine(model: str = NOMIC) -> SimpleNamespace:
    return SimpleNamespace(_embedder=SimpleNamespace(model_name=model))


def _config(setting: str) -> SimpleNamespace:
    return SimpleNamespace(retrieval=SimpleNamespace(answer_support_recipe=setting))


def test_auto_is_todays_model_key_mapping() -> None:
    assert sr.ACTIVE is sr.ANSWER_SUPPORT_V2
    assert sr.select("auto", NOMIC) is sr.ACTIVE
    assert sr.select("auto", "unknown/model") is sr.ACTIVE  # no fit of its own
    assert support_stage.recipe_for(_engine()) is sr.ACTIVE
    assert support_stage.recipe_for(_engine(), _config("auto")) is sr.ACTIVE


def test_the_config_knob_defaults_to_auto() -> None:
    from superlocalmemory.core.config import RetrievalConfig

    assert RetrievalConfig().answer_support_recipe == "auto"


def test_cjk_v1_is_the_registered_block_field_by_field() -> None:
    chosen = sr.select("cjk-v1", NOMIC)
    assert chosen is sr.ANSWER_SUPPORT_CJK_V1 is sr.recipe("answer-support-cjk-v1")
    assert chosen.version == "answer-support-cjk-v1"
    assert dict(chosen.weights) == {
        "hub_corrected": 9.1744, "keyword_coverage": 3.0, "cross_encoder": 0.3006,
        "log_bm25": 0.0, "entity_hit": 0.9276, "oov_phrase": 0.0, "rare_miss": -4.156,
        "nonsense_similarity": -16.6133, "orphan_terms": -5.4854,
        "unframed_miss": -10.1838}
    assert chosen.intercept == 9.1836
    assert chosen.threshold == 0.2760
    assert chosen.ce_clip == (-12.0, 10.0)
    assert chosen.ce_missing == 6.98
    assert chosen.hub_missing_baseline == 0.544
    assert chosen.hub_corrected_unmeasured == 0.1
    assert chosen.nonsense_similarity_missing == 0.37
    assert chosen.laya_unsupported == -1.5
    assert chosen.metadata_bonus == 0.5
    assert chosen.fitted_on == (
        "answerability-corpus-cjk-v1/8124ea49a983b9b7/answer-support-split-v1")
    assert chosen.held_out["all"] == (
        "real kept 30/30, nonsense abstain 26/26, no-answer abstain 11/14")


def test_an_explicit_choice_wins_over_the_model_key() -> None:
    assert sr.select("cjk-v1", NOMIC) is sr.ANSWER_SUPPORT_CJK_V1
    assert sr.select("v2", NOMIC) is sr.ANSWER_SUPPORT_V2
    assert support_stage.recipe_for(_engine(), _config("cjk-v1")) is sr.ANSWER_SUPPORT_CJK_V1
    assert support_stage.recipe_for(_engine(), _config("v2")) is sr.ANSWER_SUPPORT_V2


def test_an_unknown_value_falls_back_to_auto_with_a_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=sr.logger.name):
        assert sr.select("cjk-v2", NOMIC) is sr.ACTIVE
        assert support_stage.recipe_for(_engine(), _config("nope")) is sr.ACTIVE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "cjk-v2" in warnings[0].getMessage() and "auto" in warnings[0].getMessage()
    assert "nope" in warnings[1].getMessage()


def test_selection_reads_nothing_but_its_arguments_under_the_kill_switch(
        monkeypatch) -> None:
    monkeypatch.setenv("SLM_RECALL_NO_SUPPORT_GATE", "1")
    assert sr.select("cjk-v1", NOMIC) is sr.ANSWER_SUPPORT_CJK_V1
    assert support_stage.recipe_for(_engine(), _config("cjk-v1")) is sr.ANSWER_SUPPORT_CJK_V1
    # the switch still governs the gate itself; choosing stays callable
    assert support_stage.enabled(_config("auto")) is False
