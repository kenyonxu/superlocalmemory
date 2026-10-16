"""Integration regressions found by independent WP-6 review."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_provider_only_migration_config_can_build_recipe(monkeypatch):
    from superlocalmemory.core import engine_wiring
    from superlocalmemory.core.config import EmbeddingConfig
    from superlocalmemory.core.embedding_recipe import TASK_SUFFIX, RecipeEmbedder

    # **kw: the fork's wiring forwards the configured proxy to the service
    # embedder; the recipe-build assertion does not care about it.
    monkeypatch.setattr(engine_wiring, "_try_service_embedder", lambda *args, **kw: Mock())
    config = SimpleNamespace(
        embedding=EmbeddingConfig(
            model_name="nomic-ai/nomic-embed-text-v1.5" + TASK_SUFFIX,
            provider="sentence-transformers",
        )
    )
    assert isinstance(engine_wiring.init_embedder(config), RecipeEmbedder)


def test_explicit_approval_cannot_borrow_a_project_from_another_sentence():
    from superlocalmemory.retrieval.explicit_answer import attribution_support

    memory = "Cedar release is pending. Alice Chen approved the Beacon release."
    assert not attribution_support("Who approved the Cedar release?", memory)


def test_deferred_set_check_uses_the_whole_set():
    from superlocalmemory.core import answer_check_deferred as deferred
    from superlocalmemory.core import answer_check_memo as memo
    from superlocalmemory.retrieval.answer_check_status import JudgeOutcome
    from superlocalmemory.retrieval.judge_recipe import RECIPE_SET_V2
    from superlocalmemory.retrieval.sufficiency import SufficiencyVerdict

    class Judge:
        backend = "laya"
        ready = True
        closed = False
        _recipe = RECIPE_SET_V2
        threshold = 0.6

        def assess_if_idle(self, query, documents):
            probability = 0.9 if len(documents) == 2 else 0.1
            return JudgeOutcome(SufficiencyVerdict((probability,), 0.6, "set-v2"), "judged")

    judge = Judge()
    documents = ("Team is Cedar", "Date is Sunday")
    key = memo.key_for(judge, "Which team and date?", documents)
    deferred._finish(judge, memo._memo_for(judge), key, "Which team and date?", documents, 1e20)
    verdict = memo.lookup(judge, "Which team and date?", documents)
    assert verdict is not None and not verdict.insufficient
    memo.clear(judge)


def test_raw_semantic_evidence_is_not_fisher_probability(monkeypatch):
    from superlocalmemory.retrieval.semantic_channel import SemanticChannel
    from superlocalmemory.storage.models import AtomicFact

    fact = AtomicFact(fact_id="noise", embedding=[0.0, 1.0], fisher_variance=[1.0, 1.0])
    db = Mock()
    db.get_all_facts.return_value = [fact]
    channel = SemanticChannel(db)
    monkeypatch.setattr(channel, "_fisher_weight", lambda count: 1.0)
    monkeypatch.setattr(channel, "_compute_fisher_sim", lambda *args: 0.99)
    assert channel.search([1.0, 0.0], "default")[0][1] == pytest.approx(0.5)


def test_eg2_uses_store_measurement_instead_of_provisional_floor(tmp_path, monkeypatch):
    import json

    from superlocalmemory.retrieval.text_floor import text_semantic_floor
    from superlocalmemory.runtimes.media_models import EG2_REPO

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    payload = {
        "schema": "superlocalmemory.answer-check-calibration/v1",
        "entries": [
            {
                "backend": "laya",
                "recipe_id": "sufficiency-v1",
                "threshold": 0.6,
                "answered": 28,
                "unanswered": 14,
            }
        ],
        "semantic": [
            {"signature": EG2_REPO + "::768", "raw_floor": 0.72, "answered": 28, "unanswered": 14}
        ],
    }
    (tmp_path / "answer_check_calibration.json").write_text(json.dumps(payload))
    assert text_semantic_floor(SimpleNamespace(model_name=EG2_REPO, dimension=768), 0.60) == 0.72


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema": "superlocalmemory.answer-check-calibration/v1", "entries": [1], "semantic": [1]},
    ],
)
def test_bad_calibration_shapes_are_ignored(tmp_path, monkeypatch, payload):
    import json

    from superlocalmemory.retrieval.embedding_calibration import floor_for
    from superlocalmemory.retrieval.laya_set_recipe import calibrated_recipe

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    (tmp_path / "answer_check_calibration.json").write_text(json.dumps(payload))
    assert floor_for(tmp_path, "model::768") is None
    assert calibrated_recipe(SimpleNamespace(_model="model", top_k=3)) is None


def test_removed_set_calibration_restores_baseline(tmp_path, monkeypatch):
    from superlocalmemory.core.answer_calibration import write_calibration
    from superlocalmemory.retrieval.judge_recipe import RECIPE_V1
    from superlocalmemory.retrieval.laya_set_recipe import refresh
    from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    judge = LayaSufficiencyJudge(start=False)
    write_calibration(
        tmp_path,
        {
            "backend": "laya",
            "recipe_id": "sufficiency-set-v2",
            "threshold": 0.7,
            "answered": 28,
            "unanswered": 14,
            "model": judge._model,
            "top_k": 3,
        },
    )
    refresh(judge)
    assert judge._recipe.recipe_id == "sufficiency-set-v2"
    (tmp_path / "answer_check_calibration.json").unlink()
    refresh(judge)
    assert judge._recipe == RECIPE_V1
    assert judge.calibration_status != "measured_on_this_store"
    judge.shutdown()


def test_scoped_record_cannot_launder_an_unapproved_environment():
    from superlocalmemory.retrieval.explicit_answer import attribution_support

    assert not attribution_support(
        "Who approved Cedar_123 staging recovery checklist?",
        "Cedar_123 staging notes. Alice Chen approved the recovery checklist.",
    )


def test_calibration_refresh_does_not_mutate_an_inflight_request(monkeypatch):
    import threading

    from superlocalmemory.retrieval import laya_set_recipe
    from superlocalmemory.retrieval.judge_recipe import RECIPE_V1

    judge = SimpleNamespace(
        _lock=threading.Lock(), _recipe=RECIPE_V1, threshold=0.6, _model="fixture", top_k=3
    )
    monkeypatch.setattr(laya_set_recipe, "calibrated_recipe", lambda j: 0.7)
    with judge._lock:
        laya_set_recipe.refresh(judge)
        assert judge._recipe is RECIPE_V1
        assert judge.threshold == 0.6


def test_deferred_new_set_calibration_never_combines_singleton_set_judgments(monkeypatch):
    from superlocalmemory.core import answer_check_deferred as deferred
    from superlocalmemory.core import answer_check_memo as memo
    from superlocalmemory.retrieval import laya_set_recipe
    from superlocalmemory.retrieval.answer_check_status import JudgeOutcome
    from superlocalmemory.retrieval.judge_recipe import RECIPE_SET_V2, RECIPE_V1
    from superlocalmemory.retrieval.sufficiency import SufficiencyVerdict

    calls = []

    class Judge:
        backend, ready, closed, threshold = "laya", True, False, 0.6
        _recipe = RECIPE_V1

        def assess_if_idle(self, query, documents):
            self._recipe = RECIPE_SET_V2
            calls.append(len(documents))
            return JudgeOutcome(
                SufficiencyVerdict((0.9 if len(documents) == 2 else 0.1,), 0.6, "set-v2"), "judged"
            )

    monkeypatch.setattr(laya_set_recipe, "refresh", lambda judge: None)
    judge = Judge()
    docs = ("Team is Cedar.", "Date is Sunday.")
    key = memo.key_for(judge, "Which team and date?", docs)
    deferred._finish(judge, memo._memo_for(judge), key, "Which team and date?", docs, 1e20)
    verdict = memo.lookup(judge, "Which team and date?", docs)
    assert verdict is not None and not verdict.insufficient
    assert calls[-1] == 2
    memo.clear(judge)


def test_a_completed_old_recipe_cannot_be_memoized_as_the_new_calibration():
    import threading

    from superlocalmemory.core import answer_check_memo as memo
    from superlocalmemory.retrieval.sufficiency import SufficiencyVerdict

    class Judge:
        backend, ready, closed, threshold, top_k = "laya", True, False, 0.8, 3
        calibration_id = "new-set"
        _lock = threading.Lock()

    judge = Judge()
    memo.store(judge, "a question?", ["a memory"], SufficiencyVerdict((0.9,), 0.6, "old-v1"))
    assert memo.lookup(judge, "a question?", ["a memory"]) is None
    memo.clear(judge)
