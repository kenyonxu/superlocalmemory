# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Train/holdout threshold measurement shared by judge and embedding spaces."""

from __future__ import annotations

import math
from collections.abc import Sequence


def rates(positive: Sequence[float], negative: Sequence[float], threshold: float) -> dict:
    kept = sum(p >= threshold for p in positive)
    rejected = sum(p < threshold for p in negative)
    false_reject = len(positive) - kept
    return {
        "keep_rate": kept / len(positive) if positive else None,
        "reject_rate": rejected / len(negative) if negative else None,
        "abstention_precision": rejected / (rejected + false_reject)
        if rejected + false_reject
        else None,
        "abstention_recall": rejected / len(negative) if negative else None,
    }


def calibrate(positive: Sequence[float], negative: Sequence[float]) -> dict:
    """Last quarter held out; maximize balanced accuracy on training ONLY.

    Ties prefer retaining answers, then the lower threshold. Report the holdout
    even when it is bad; never feed it back into the chosen threshold.
    """
    if len(positive) < 4 or len(negative) < 4:
        raise ValueError("At least four examples of each label are required")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in (*positive, *negative)):
        raise ValueError("Calibration scores must be finite values between 0 and 1")
    pcut, ncut = (
        len(positive) - max(1, len(positive) // 4),
        len(negative) - max(1, len(negative) // 4),
    )
    train_p, train_n = positive[:pcut], negative[:ncut]
    candidates = sorted({0.0, 1.0, *train_p, *train_n})

    def key(threshold):
        measured = rates(train_p, train_n, threshold)
        keep, reject = measured["keep_rate"], measured["reject_rate"]
        return ((keep + reject) / 2, keep, -threshold)

    threshold = max(candidates, key=key)
    return {
        "threshold": threshold,
        "training": rates(train_p, train_n, threshold),
        "held_out": rates(positive[pcut:], negative[ncut:], threshold),
        "train_answered": pcut,
        "train_unanswered": ncut,
        "held_out_answered": len(positive) - pcut,
        "held_out_unanswered": len(negative) - ncut,
    }
