# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The answer-support recipe is chosen by the text model it was fitted on.

answer-support-v2 was fitted on nomic cosines only. On the author's store, after
"Upgrade memory engine" moved text to EmbeddingGemma 2, it scored a correct
memory at 0.008 against a 0.30 bar and recall said "That isn't in
SuperLocalMemory" to questions it had answered that morning (13 Oct 2026).

So: a model with a fitted recipe uses it; nothing may move text onto a managed
model that has none (the upgrade plan says why, an explicit switch needs
force); and every other model keeps today's recipe, so a patch release changes
no one else's recall.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from superlocalmemory.core import engine_upgrade, support_fit_guard
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.support_stage import recipe_for
from superlocalmemory.retrieval import support_recipe as sr

NOMIC = "nomic-ai/nomic-embed-text-v1.5"
GEMMA = "google/embeddinggemma-2"


@pytest.fixture
def no_gemma_fit(monkeypatch):
    """Whatever this build ships, measure the rule as if Gemma had no fit."""
    monkeypatch.setattr(sr, "_BY_MODEL", {NOMIC: sr.ANSWER_SUPPORT_V2})
    monkeypatch.setattr(sr, "APPROVED_FOR_TEXT", frozenset({NOMIC}))


def test_nomic_has_its_fitted_recipe():
    assert sr.for_model(NOMIC) is sr.ANSWER_SUPPORT_V2
    assert sr.for_model(f"{NOMIC}::768") is sr.ANSWER_SUPPORT_V2
    assert sr.for_model("nomic-embed-text-v1.5") is sr.ANSWER_SUPPORT_V2


def test_a_model_never_fitted_has_none(no_gemma_fit):
    assert sr.for_model(GEMMA) is None
    assert sr.for_model("") is None


def test_recall_uses_the_live_models_recipe(monkeypatch):
    other = sr.ANSWER_SUPPORT_V1
    monkeypatch.setattr(sr, "_BY_MODEL", {NOMIC: sr.ANSWER_SUPPORT_V2, GEMMA: other})
    engine = SimpleNamespace(_embedder=SimpleNamespace(model_name=GEMMA))
    assert recipe_for(engine) is other


def test_any_other_model_keeps_todays_recipe(no_gemma_fit):
    for embedder in (SimpleNamespace(model_name="text-embedding-3-small"), None):
        assert recipe_for(SimpleNamespace(_embedder=embedder)) is sr.ACTIVE


def test_moving_text_to_an_unfitted_managed_model_is_refused(no_gemma_fit):
    target = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    reason = support_fit_guard.refusal_for(target)
    assert reason and "force" in reason


def test_fitted_or_unmanaged_targets_are_not_refused(no_gemma_fit):
    assert support_fit_guard.refusal_for(EmbeddingConfig(model_name=NOMIC)) is None
    openai = EmbeddingConfig(model_name="text-embedding-3-small", dimension=1536, provider="openai")
    assert support_fit_guard.refusal_for(openai) is None


def test_the_upgrade_plan_says_why_it_cannot_start(no_gemma_fit):
    live = EmbeddingConfig(model_name=NOMIC, dimension=768, provider="sentence-transformers")
    p = engine_upgrade.plan(live, 1000, "ready", media_enabled=True)
    assert p["available"] is False
    assert p["reason"] == support_fit_guard.refusal_for(engine_upgrade.upgrade_target(live))


def test_the_upgrade_plan_opens_once_the_model_is_fitted(monkeypatch):
    monkeypatch.setattr(sr, "_BY_MODEL", {NOMIC: sr.ANSWER_SUPPORT_V2, GEMMA: sr.ANSWER_SUPPORT_V2})
    monkeypatch.setattr(sr, "APPROVED_FOR_TEXT", frozenset({NOMIC, GEMMA}))
    live = EmbeddingConfig(model_name=NOMIC, dimension=768, provider="sentence-transformers")
    assert engine_upgrade.plan(live, 1000, "ready", media_enabled=True)["available"] is True


def test_an_explicit_switch_is_refused_unless_forced(no_gemma_fit):
    import json

    from superlocalmemory.server.routes.embedding_reindex import unfitted_refusal

    target = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    refused = unfitted_refusal({"model_name": GEMMA}, target)
    assert refused is not None and refused.status_code == 409
    assert json.loads(refused.body)["error"] == "not_fitted"
    assert unfitted_refusal({"model_name": GEMMA, "force": True}, target) is None


def test_the_cli_sends_force_only_when_asked(monkeypatch):
    from argparse import Namespace

    from superlocalmemory.cli import embedder_cmd

    bodies = []
    monkeypatch.setattr(embedder_cmd, "daemon_request",
                        lambda method, path, body=None, **kw: bodies.append(body) or {
                            "success": True, "job": {"job_id": 1, "kind": "switch",
                                                     "state": "queued", "from": "a::1", "to": "b::2",
                                                     "done": 0, "total": 1}})
    base = dict(embedder_command="switch", json=False, model=GEMMA, dimension=0,
                provider="", endpoint="", no_wait=True)
    embedder_cmd.cmd_embedder(Namespace(**base))
    embedder_cmd.cmd_embedder(Namespace(**base, force=True))
    assert "force" not in bodies[0]
    assert bodies[1]["force"] is True


# -- a store that already moved (4.2.0 / 4.2.1 upgraded by itself after an npm install) --

def test_a_store_already_on_an_unfitted_model_is_told_how_to_go_back(no_gemma_fit):
    live = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    p = engine_upgrade.plan(live, 1000, "ready", media_enabled=True)
    assert p["already"] is True and p["available"] is False
    assert "slm embedder rollback" in p["reason"]
    assert p["reason"] == support_fit_guard.live_advice(live)


def test_a_store_on_a_fitted_model_reads_as_before(monkeypatch):
    monkeypatch.setattr(sr, "_BY_MODEL", {NOMIC: sr.ANSWER_SUPPORT_V2, GEMMA: sr.ANSWER_SUPPORT_V2})
    monkeypatch.setattr(sr, "APPROVED_FOR_TEXT", frozenset({NOMIC, GEMMA}))
    live = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    assert support_fit_guard.live_advice(live) is None
    assert engine_upgrade.plan(live, 1000, "ready")["reason"] == engine_upgrade._ALREADY


def test_doctor_warns_a_store_on_an_unfitted_model(no_gemma_fit, tmp_path):
    import json
    import sqlite3

    from superlocalmemory.cli.doctor_models import answer_check_fit_finding

    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE embedding_space (id INTEGER PRIMARY KEY, live_signature TEXT, "
                 "live_config TEXT)")
    conn.execute("INSERT INTO embedding_space VALUES (1, ?, ?)", (f"{GEMMA}::768", json.dumps(
        {"model_name": GEMMA, "provider": "slm-media", "dimension": 768})))
    conn.commit()
    conn.close()
    status, detail, fix = answer_check_fit_finding(db)
    assert status == "WARN" and GEMMA in detail and fix == "slm embedder rollback"


def test_doctor_passes_a_fitted_or_unrecorded_store(no_gemma_fit, tmp_path):
    from superlocalmemory.cli.doctor_models import answer_check_fit_finding

    assert answer_check_fit_finding(tmp_path / "missing.db")[0] == "PASS"



# -- what 4.2.2 actually ships, unpatched ------------------------------------------

def test_shipped_gemma_recipe_is_recorded_but_never_used():
    """eg2-v1 failed the frozen half (it answers questions with no answer), so
    recall on Gemma keeps v2 and says "not in memory" out loud instead."""
    assert sr.recipe("answer-support-eg2-v1") is sr.ANSWER_SUPPORT_EG2_V1
    assert sr.for_model(GEMMA) is None
    assert sr.approved_for_text(NOMIC) and not sr.approved_for_text(GEMMA)
    engine = SimpleNamespace(_embedder=SimpleNamespace(model_name=GEMMA))
    assert recipe_for(engine) is sr.ACTIVE


def test_shipped_switch_to_gemma_is_refused_with_the_reason():
    target = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    reason = support_fit_guard.refusal_for(target)
    assert reason and "not calibrated" in reason and "force" in reason
    live = EmbeddingConfig(model_name=NOMIC, dimension=768, provider="sentence-transformers")
    assert engine_upgrade.plan(live, 1000, "ready", media_enabled=True)["available"] is False


def test_shipped_store_already_on_gemma_is_told_how_to_go_back():
    live = EmbeddingConfig(model_name=GEMMA, dimension=768, provider="slm-media")
    advice = support_fit_guard.live_advice(live)
    assert advice and "not calibrated" in advice and "slm embedder rollback" in advice


def test_the_gemma_recipe_keeps_a_real_answer_that_v2_refused():
    """Measured, not made up: question r-code-1's expected memory on the Gemma
    harness store (13 Oct 2026). v2 scored it 0.0165; eg2-v1 scores it 0.9995."""
    from superlocalmemory.retrieval import support_gate

    evidence = {"hub_corrected": 0.206152, "keyword_coverage": 0.75, "ce_raw": 9.7855,
                "rare_miss": 0.0, "bm25_raw": 16.397183, "cosine": 0.876895,
                "entity_hit": 1.0, "subject_coverage": 1.0}
    query = {"nonsense_similarity": 0.598335, "orphan_terms": 0.0, "oov_phrase": False,
             "query_terms": 4}
    v2 = support_gate.probability(evidence, query, sr.ANSWER_SUPPORT_V2)
    eg2 = support_gate.probability(evidence, query, sr.ANSWER_SUPPORT_EG2_V1)
    assert v2 < sr.ANSWER_SUPPORT_V2.threshold
    assert eg2 >= sr.ANSWER_SUPPORT_EG2_V1.threshold
