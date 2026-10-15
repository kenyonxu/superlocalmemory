"""Store calibration is local, labelled, held out and refuses small samples."""

import json

import pytest


def test_threshold_is_chosen_on_training_only():
    from superlocalmemory.retrieval.calibration_metrics import calibrate

    positives = [0.8, 0.9, 0.7, 0.1]
    negatives = [0.2, 0.3, 0.4, 0.95]
    result = calibrate(positives, negatives)
    assert result["threshold"] == pytest.approx(0.7)
    assert result["held_out"]["keep_rate"] == 0
    assert result["held_out"]["reject_rate"] == 0


def test_calibration_writes_loader_compatible_file_atomically(tmp_path):
    from superlocalmemory.core.answer_calibration import write_calibration
    from superlocalmemory.retrieval.judge_calibration_file import measured_thresholds

    entry = {
        "backend": "laya",
        "recipe_id": "sufficiency-v1",
        "threshold": 0.6,
        "answered": 20,
        "unanswered": 10,
    }
    write_calibration(tmp_path, entry)
    path = tmp_path / "answer_check_calibration.json"
    assert measured_thresholds(path)[("laya", "sufficiency-v1")] == 0.6
    assert json.loads(path.read_text())["schema"].endswith("/v1")


def test_explicit_attribution_survives_low_recorded_judge_probability():
    from superlocalmemory.retrieval.explicit_answer import attribution_support

    memory = (
        "OPS_SOAK4_FINAL_00001: Fictional Cedar deployment 1 uses port 6101. "
        "Alice Chen approved the synthetic recovery checklist."
    )
    for question in (
        "Who approved the synthetic Cedar recovery checklist in OPS_SOAK4_FINAL_00001?",
        "Who approved the checklist for OPS_SOAK4_FINAL_00001?",
    ):
        assert attribution_support(question, memory)
    assert not attribution_support("Who approved the checklist for OPS_SOAK4_FINAL_00002?", memory)
    assert not attribution_support(
        "Who approved the checklist for OPS_SOAK4_FINAL_00001?",
        memory.replace("approved", "did not approve"),
    )


def test_set_recipe_asks_one_question_about_the_whole_set():
    from superlocalmemory.retrieval.judge_recipe import RECIPE_SET_V2
    from superlocalmemory.retrieval.laya_set_recipe import request_for

    request = request_for(
        RECIPE_SET_V2, "Which team and date?", ["Team is Cedar.", "Date is Sunday."]
    )
    assert len(request["documents"]) == 1
    assert "Team is Cedar" in request["documents"][0]
    assert "Date is Sunday" in request["documents"][0]


def test_hand_edit_notice_survives_status_contract():
    from superlocalmemory.retrieval.answer_check_status import (
        ANSWER_CHECK_DETAILS,
        answer_check_note,
    )

    assert "settings_changed" in ANSWER_CHECK_DETAILS
    assert "Restart" in answer_check_note("unavailable", "settings_changed")
