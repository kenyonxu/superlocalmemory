"""PR-253 #2: identical holdouts protect the active recipe and floor."""
import json

import pytest

from superlocalmemory.core.answer_calibration import CalibrationJob, write_calibration
from superlocalmemory.core.calibration_measure import _fit
from tests.test_core.test_wp6_calibration_job import _engine

POSITIVE = [0.9] * 21 + [0.65] * 3 + [0.9] * 4
NEGATIVE = [0.85] * 11 + [0.1] * 3


def test_exact_review_fit_rejects_loss_of_working_answers():
    with pytest.raises(ValueError, match="regression"):
        _fit("fixture::768", POSITIVE, NEGATIVE, POSITIVE, NEGATIVE, 0)


def test_exact_review_job_keeps_previous_file_recipe_and_memo(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples, calibration_measure
    from superlocalmemory.retrieval.laya_set_recipe import refresh

    engine = _engine(tmp_path)
    judge = engine._retrieval_engine._sufficiency_judge
    write_calibration(tmp_path, {
        "backend": "laya", "recipe_id": "sufficiency-set-v2", "threshold": 0.6,
        "answered": 28, "unanswered": 14, "model": judge._model, "top_k": 3,
    }, {"signature": "nomic-ai/nomic-embed-text-v1.5::768", "raw_floor": 0.6,
        "answered": 28, "unanswered": 14})
    judge._answer_check_root = tmp_path
    refresh(judge)
    previous = (tmp_path / "answer_check_calibration.json").read_bytes()
    examples = [{"answered": True, "score": p} for p in POSITIVE]
    examples += [{"answered": False, "score": p} for p in NEGATIVE]
    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: examples)

    def sample(engine, judge, example, stop, **kwargs):
        baseline = kwargs.get("baseline")
        if baseline is not None:
            baseline["kept"] = example["score"] >= 0.6
        return example["answered"], example["score"], [example["score"]]

    monkeypatch.setattr(calibration_measure, "_sample", sample)
    job = CalibrationJob()
    try:
        job.start(engine)
        job._thread.join(3)
        assert job.status()["state"] == "failed", job.status()
        assert "regression" in job.status()["detail"]
        assert (tmp_path / "answer_check_calibration.json").read_bytes() == previous
        assert judge.threshold == 0.6
        assert judge._recipe.recipe_id == "sufficiency-set-v2"
        assert json.loads(previous)["semantic"][0]["raw_floor"] == 0.6
    finally:
        job.stop()


def test_semantic_regression_preserves_floor_even_with_a_good_judge_candidate():
    measured = _fit("fixture::768", [0.9] * 28, [0.1] * 14, POSITIVE, NEGATIVE, 0)
    assert not measured["semantic"]["usable"]
    assert measured["embedding_status"] == "held_out_regression"
    assert measured["baseline"]["semantic"]["keep_rate"] == 1


def test_candidate_recipe_is_compared_with_current_recipe_decisions(tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples, calibration_measure
    engine = _engine(tmp_path)
    # Candidate returns .65 on these real answers and .10 on absent examples;
    # fitting .65 keeps them all. Baseline can already reject every absent query.
    engine._retrieval_engine._sufficiency_judge.threshold = 0.95
    examples = [{"answered": True, "score": p} for p in POSITIVE]
    examples += [{"answered": False, "score": p} for p in NEGATIVE]
    monkeypatch.setattr(calibration_examples, "build_examples", lambda *a: examples)

    def sample(engine, judge, example, stop, *, baseline):
        baseline["kept"] = example["answered"]  # actual prior decisions, different recipe
        return example["answered"], example["score"], []

    monkeypatch.setattr(calibration_measure, "_sample", sample)
    job = CalibrationJob()
    try:
        job.start(engine)
        job._thread.join(3)
        assert job.status()["state"] == "failed"
        assert "regression" in job.status()["detail"]
        assert not (tmp_path / "answer_check_calibration.json").exists()
    finally:
        job.stop()
