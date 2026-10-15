# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Evaluate actual candidate SETS; operational skips never become negative labels."""

from __future__ import annotations

import time

from superlocalmemory.core.calibration_candidates import raw_semantic, source_score
from superlocalmemory.core.calibration_control import await_idle
from superlocalmemory.core.calibration_guard import (
    baseline_rates,
    current_kept,
    file_stamp,
    judge_identity,
    live_floor,
    nonregressing,
)
from superlocalmemory.core.recall_gate import background_work
from superlocalmemory.retrieval.calibration_metrics import calibrate
from superlocalmemory.retrieval.judge_recipe import (
    RECIPE_SET_V2,
    clean_documents,
    document_from_fact,
)
from superlocalmemory.retrieval.laya_set_recipe import calibration_probability, request_for
from superlocalmemory.storage.embedding_spaces import signature_of


def _sample(engine, judge, example, stop, *, baseline=None):
    from superlocalmemory.core.answer_check_scope import skip_answer_check

    await_idle(stop)
    with stop.operation(), background_work(), skip_answer_check():
        from superlocalmemory.storage.models import Mode

        response = engine._retrieval_engine.recall(
            example["question"], engine.profile_id, mode=Mode.A, limit=judge.top_k
        )  # local channels only: no LLM expansion, bandit plays, hooks or outcomes
        raw_facts, scores = raw_semantic(engine, example["question"], judge.top_k)
        if example["answered"]:
            scores = source_score(engine, example["question"], example["fact_id"])
    documents = [document_from_fact(r.fact) for r in response.results[: judge.top_k]]
    sources = {r.fact.fact_id for r in response.results[:judge.top_k]}
    if not documents:
        documents = [document_from_fact(fact) for fact in raw_facts]
        sources = {fact.fact_id for fact in raw_facts}
    documents = clean_documents(documents)
    shown = request_for(RECIPE_SET_V2, example["question"], [d.content for d in documents])
    answered = example["answered"] and example["answer"] in shown["documents"][0]
    if example["answered"] and (example["fact_id"] not in sources or not answered):
        return False, None, scores  # a missing source is not a proved negative label
    await_idle(stop)
    probability = (
        calibration_probability(judge, example["question"], documents) if documents else None
    )
    if baseline is not None and probability is not None:
        baseline["kept"] = current_kept(judge, example["question"], documents)
        if baseline["kept"] is None:
            probability = None  # both recipes must judge the identical labelled sample
    return answered, probability, scores


def _fit(identity, positive, negative, semantic_p, semantic_n, skipped, *,
         current_threshold=0.6, semantic_floor=0.6, kept=None):
    if len(positive) < 28 or len(negative) < 14:
        raise ValueError("Too few judged labels (need 28 answered / 14 absent); nothing replaced")
    result = {
        "judge": calibrate(positive, negative),
        "answered": len(positive),
        "unanswered": len(negative),
        "skipped": skipped,
        "embedding_status": "insufficient_source_scores",
    }
    holdout = result["judge"]["held_out"]
    if ((holdout["keep_rate"] + holdout["reject_rate"]) / 2 <= 0.5
            or min(holdout["keep_rate"], holdout["reject_rate"]) < 0.5):
        raise ValueError("Poor held-out discrimination; previous calibration kept")
    prior = baseline_rates(positive, negative, result["judge"], current_threshold, kept)
    result["baseline"] = {"judge": prior}
    if not nonregressing(holdout, prior):
        raise ValueError("Held-out regression; previous calibration kept")
    _fit_semantic(result, identity, semantic_p, semantic_n, semantic_floor)
    return result


def _fit_semantic(result, identity, positive, negative, floor):
    if len(positive) < 28 or len(negative) < 14:
        return
    fit = calibrate(positive, negative)
    heldout = fit["held_out"]
    baseline = baseline_rates(positive, negative, fit, floor)
    usable = ((heldout["keep_rate"] + heldout["reject_rate"]) / 2 > 0.5
              and min(heldout["keep_rate"], heldout["reject_rate"]) >= 0.5
              and nonregressing(heldout, baseline))
    result["baseline"]["semantic"] = baseline
    result["embedding_status"] = "measured" if usable else "held_out_regression"
    result["semantic"] = {
        "usable": usable, "signature": identity, "raw_floor": fit["threshold"],
        "answered": len(positive), "unanswered": len(negative),
        "metrics": fit, "measured_at": time.time(),
    }


def measure_examples(engine, judge, examples, stop, progress):
    identity = signature_of(engine._config.embedding)
    positive, negative, semantic_p, semantic_n = [], [], [], []
    skipped = 0
    initial_judge = judge_identity(judge)
    kept_p, kept_n = [], []
    floor, stamp = live_floor(engine), file_stamp(engine._config.base_dir)
    for index, example in enumerate(examples):
        if stop.is_set():
            break
        if signature_of(engine._config.embedding) != identity:
            raise ValueError("Embedding space changed during calibration; nothing replaced")
        if engine._retrieval_engine._sufficiency_judge is not judge:
            raise ValueError("Answer check changed during calibration; nothing replaced")
        if judge_identity(judge) != initial_judge:
            raise ValueError("Active calibration changed during measurement; nothing replaced")
        baseline = {}
        answered, probability, scores = _sample(engine, judge, example, stop, baseline=baseline)
        if probability is None:
            skipped += 1
        else:
            (positive if answered else negative).append(probability)
            (kept_p if answered else kept_n).append(baseline["kept"])
        # Embedding measurement is independent of judge availability and uses
        # the actual source label, not whether a clipped reader saw the quote.
        if scores:
            (semantic_p if example["answered"] else semantic_n).append(max(scores))
        progress(done=index + 1, total=len(examples), skipped=skipped,
                 detail="Comparing candidate and current decisions on held-out sets")
        stop.wait(0.01)
    result = _fit(identity, positive, negative, semantic_p, semantic_n, skipped,
                  current_threshold=initial_judge[1], semantic_floor=floor,
                  kept=(kept_p, kept_n))
    result["baseline"].update(judge_identity=initial_judge, semantic_floor=floor, file_stamp=stamp)
    return result
