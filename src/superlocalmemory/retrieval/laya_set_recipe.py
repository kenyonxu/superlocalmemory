# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Set judgement is its own recipe; never borrow single-document thresholds."""

from __future__ import annotations

from superlocalmemory.retrieval.judge_recipe import RECIPE_SET_V2


def request_for(recipe, query, rendered):
    documents = rendered
    if recipe.recipe_id == RECIPE_SET_V2.recipe_id:
        documents = [
            "\n".join(f"Memory {i + 1}: {text[:500]}" for i, text in enumerate(rendered))[:1800]
        ]
    return {"cmd": "judge", "query": query, "documents": documents, "question": recipe.question}


def calibrated_recipe(judge):
    """Select set-v2 only after this store has measured it for these weights/K."""
    import json

    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.retrieval.judge_calibration_file import (
        FILE_NAME,
        measured_thresholds,
        parse_entries,
    )

    try:
        root = getattr(judge, '_answer_check_root', None)
        from pathlib import Path
        path = Path(root) / FILE_NAME if root is not None else state_path(FILE_NAME)
        payload = json.loads(path.read_text(encoding="utf-8"))
        parse_entries(payload)
        entries = payload["entries"]
        matched = any(
            e.get("backend") == "laya"
            and e.get("recipe_id") == RECIPE_SET_V2.recipe_id
            and e.get("model") == judge._model
            and e.get("top_k") == judge.top_k
            for e in entries
        )
        if not matched:
            return None
        threshold = measured_thresholds(path).get(("laya", RECIPE_SET_V2.recipe_id))
        return threshold
    except (OSError, ValueError, TypeError):
        return None


def refresh(judge):
    threshold = calibrated_recipe(judge)
    lock = getattr(judge, '_lock', None)
    if lock is not None and not lock.acquire(blocking=False):
        return  # in-flight requests retain one recipe/threshold/identity tuple
    try:
        _apply_threshold(judge, threshold)
    finally:
        if lock is not None:
            lock.release()


def _apply_threshold(judge, threshold):
    if threshold is None:
        if getattr(judge, "_automatic_set_calibration", False):
            _restore_baseline(judge)
        return
    changed = judge._recipe != RECIPE_SET_V2 or judge.threshold != threshold
    if changed:
        from superlocalmemory.core.answer_check_memo import clear
        from superlocalmemory.retrieval.sufficiency import laya_calibration_id

        if not getattr(judge, "_automatic_set_calibration", False):
            judge._baseline_recipe = judge._recipe
        judge._automatic_set_calibration = True
        judge._recipe = RECIPE_SET_V2
        judge.calibration_id = laya_calibration_id(judge._model, RECIPE_SET_V2, judge.top_k)
        judge.threshold = threshold
        judge.calibration_status = "measured_on_this_store"
        clear(judge)


def calibration_probability(judge, question, documents):
    """Ask local worker while idle, with fresh set wording, under the live lock."""
    from superlocalmemory.retrieval.sufficiency import _valid_probabilities

    payload = request_for(RECIPE_SET_V2, question, [d.content for d in documents])
    reply = judge._background_exchange(payload, judge._timeout_s)
    if reply is None or not reply.get("ok"):
        return None
    probabilities = _valid_probabilities(reply.get("probabilities"), 1)
    return probabilities[0] if probabilities else None


def _restore_baseline(judge):
    from superlocalmemory.core.answer_check_memo import clear
    from superlocalmemory.retrieval.sufficiency import calibration_for_weights, laya_calibration_id

    recipe = judge._baseline_recipe
    calibration = calibration_for_weights(judge._model, recipe)
    judge._recipe = recipe
    judge.threshold = calibration.threshold
    judge.calibration_status = calibration.status
    judge.calibration_id = laya_calibration_id(judge._model, recipe, judge.top_k)
    judge._automatic_set_calibration = False
    clear(judge)
