# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Compare paired holdouts with the effective live decision, before publication."""
from superlocalmemory.retrieval.calibration_metrics import rates
from superlocalmemory.retrieval.judge_recipe import RECIPE_SET_V2


def judge_identity(judge):
    return (judge._recipe.recipe_id, judge.threshold, judge._model, judge.top_k)


def current_kept(judge, question, documents):
    from superlocalmemory.retrieval.laya_set_recipe import request_for
    from superlocalmemory.retrieval.source_verdict import settle
    from superlocalmemory.retrieval.sufficiency import SufficiencyVerdict, _valid_probabilities

    rendered = [judge._recipe.render(doc) for doc in documents]
    payload = request_for(judge._recipe, question, rendered)
    reply = judge._background_exchange(payload, judge._timeout_s)
    if reply is None or not reply.get("ok"):
        return None
    probabilities = _valid_probabilities(reply.get("probabilities"), len(payload["documents"]))
    if probabilities is None:
        return None
    verdict = SufficiencyVerdict(probabilities, judge.threshold, "calibration-baseline")
    return not settle(question, rendered, verdict).insufficient


def baseline_rates(positive, negative, fit, threshold, kept=None):
    pcut, ncut = fit["train_answered"], fit["train_unanswered"]
    if kept is not None:
        positive, negative = kept
        positive, negative = [float(v) for v in positive], [float(v) for v in negative]
        threshold = 0.5
    return rates(positive[pcut:], negative[ncut:], threshold)


def nonregressing(candidate, baseline):
    for metric in ("keep_rate", "reject_rate", "abstention_precision"):
        prior, proposed = baseline[metric], candidate[metric]
        if prior is not None and (proposed is None or proposed < prior):
            return False
    return True


def require_comparison(measured, judge, engine):
    baseline = measured.get("baseline")
    if not baseline or tuple(baseline["judge_identity"]) != judge_identity(judge):
        raise ValueError("Active calibration changed or held-out comparison missing")
    if baseline.get("semantic_floor") != live_floor(engine) or not baseline.get("file_stamp"):
        raise ValueError("Active embedding floor changed or comparison missing; nothing replaced")
    if not nonregressing(measured["judge"]["held_out"], baseline["judge"]):
        raise ValueError("Held-out regression; previous calibration kept")
    if measured["judge"].get("recipe_id", RECIPE_SET_V2.recipe_id) != RECIPE_SET_V2.recipe_id:
        raise ValueError("Candidate recipe changed; nothing replaced")


def file_stamp(root):
    import hashlib

    from superlocalmemory.retrieval.judge_calibration_file import FILE_NAME
    try:
        return hashlib.sha256((root / FILE_NAME).read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def live_floor(engine):
    from superlocalmemory.retrieval.text_floor import text_semantic_floor
    return text_semantic_floor(engine._embedder, engine._config.retrieval.min_semantic_evidence)
