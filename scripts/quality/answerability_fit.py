#!/usr/bin/env python3
# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Fit the answer-support recipe (retrieval/support_recipe.py) on the harness.

Reads the per-question rows that ``answerability_eval.py --rows`` writes (its
"features" pass: every question's candidates with their evidence features, the
evidence floor off) and:

  1. splits the questions once, deterministically, per set and category
     (``TRAIN_SHARE`` to training, the rest held out; sha256 of the question id);
  2. fits the per-memory logistic model on the training questions only, with
     every weight's sign fixed in advance (``SIGNS``, floors in
     ``MIN_WEIGHTS``) -- multiple-instance
     style: a real question contributes its best expected memory, a nonsense or
     no-answer question its top-scoring candidates, re-chosen each round;
  3. chooses the threshold on the held-out questions: the widest-margin point
     between the highest held-out nonsense question and the lowest held-out
     real question that recall answers today; if none exists, reports the
     trade-off curve instead of pretending;
  4. prints the held-out table per slice (all / legacy / described) and a
     recipe block to paste into support_recipe.py.

    python scripts/quality/answerability_fit.py --rows rows.jsonl [--profile a]
        [--out fit.json]

Synthetic data only. Needs numpy and scipy (development tools, not the product).
Exit codes: 0 fitted (gate holds on held-out) · 1 fitted, gate does not hold · 2 bad input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
SPLIT_VERSION = "answer-support-split-v1"
TRAIN_SHARE = 0.6
TOP_K = 5
SLICES = ("legacy", "described")
#: +1: can only raise support; -1: can only lower it.
SIGNS = {"hub_corrected": 1, "keyword_coverage": 1, "cross_encoder": 1, "log_bm25": 1,
         "entity_hit": 1, "oov_phrase": -1, "rare_miss": -1, "nonsense_similarity": -1,
         "orphan_terms": -1, "unframed_miss": -1}
#: Lower bounds on a weight (raw feature scale). Keyword coverage must be able
#: to carry an exact-keyword question when the cross-encoder is cold, the hub
#: baselines are missing and the probes are not embedded. Unbounded, the fit
#: set it to 0 (on this store the cross-encoder and the rare-term miss carry
#: the same signal). Chosen on the TRAINING split only: with every one of
#: those inputs imputed, training real questions kept rose 32 -> 41 -> 53 ->
#: 57 of 65 for floors 0, 1, 2, 3 and stayed at 57 for 4; 3 is the smallest
#: floor on that plateau.
MIN_WEIGHTS = {"keyword_coverage": 3.0}
L2 = 0.001
#: When no threshold separates the sets, keep the lowest real question by this
#: much (logits), so a recall whose features move a little still answers it.
REAL_MARGIN = 0.1
ROUNDS = 8
NEGATIVES_PER_QUESTION = 3
UNANSWERABLE_WEIGHT = 0.3


def _bootstrap() -> None:
    source = _HERE.parents[1] / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


def split_of(qid: str, share: float = TRAIN_SHARE) -> float:
    """A stable number in [0, 1) per question id."""
    h = hashlib.sha256(f"{SPLIT_VERSION}:{qid}".encode()).digest()[:8]
    return int.from_bytes(h, "big") / 2 ** 64


def split(rows: list[dict], share: float = TRAIN_SHARE) -> tuple[list[dict], list[dict]]:
    """Per (set, category): the lowest ``share`` of ids by hash train, the rest hold out."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["set"], r["category"]), []).append(r)
    train, held = [], []
    for members in groups.values():
        ordered = sorted(members, key=lambda r: split_of(r["qid"]))
        cut = max(1, round(len(ordered) * share)) if len(ordered) > 1 else 1
        train.extend(ordered[:cut])
        held.extend(ordered[cut:])
    return train, held


def hit_now(row: dict) -> bool:
    """This run's recall put an expected memory in the top 5."""
    return row["rank"] is not None and row["rank"] <= TOP_K


def answered_today(row: dict) -> bool:
    """Recall as shipped before this work answers it (``--baseline`` rows), or,
    without a baseline, this run did."""
    return bool(row["today"]) if "today" in row else hit_now(row)


def is_fresh(row: dict) -> bool:
    """A question of the frozen final test half: never fitted, never tuned on."""
    return str(row.get("source", "")).startswith("fresh_")


# -- the model inputs ------------------------------------------------------------

def _vectors(row: dict, recipe) -> list[tuple[dict, np.ndarray]]:
    from superlocalmemory.retrieval import support_gate
    from superlocalmemory.retrieval.support_recipe import FEATURES

    qe = row.get("query_evidence") or {}
    out = []
    for cand in row.get("candidates", ()):
        f = support_gate.features(cand, qe, recipe)
        out.append((cand, np.array([f[name] for name in FEATURES], dtype=float)))
    return out


def imputations(train: list[dict]) -> dict:
    """The values used when an input has no measurement, from training data only."""
    baselines = [c["hub_baseline"] for r in train for c in r.get("candidates", ())
                 if c.get("hub_baseline") is not None]
    ce = [c["ce_raw"] for r in train for c in r.get("candidates", ())[:3]
          if c.get("ce_raw") is not None]
    nonsense_profile = [r["query_evidence"]["nonsense_similarity"] for r in train
                        if r["set"] == "nonsense" and (r.get("query_evidence") or {}).get(
                            "nonsense_similarity") is not None]
    return {
        # A memory with no baseline yet is assumed as hub-like as the upper quarter.
        "hub_missing_baseline": round(float(np.percentile(baselines, 75)), 3),
        # A cross-encoder with no vote reads as the median candidate's score.
        "ce_missing": round(float(np.median(ce)), 2),
        # A question with no profile (probes not embedded yet) is assumed to read
        # like the median nonsense question: abstaining is the safe error.
        "nonsense_similarity_missing": math.ceil(float(np.median(nonsense_profile)) * 100) / 100,
    }


VECTORLESS_MARGIN = 0.10
_VECTORLESS_GRID = [round(0.02 * i, 2) for i in range(26)]  # 0.00 .. 0.50


def _keyword_only_answers(train: list[dict], recipe, value: float, threshold_logit: float,
                          coef: np.ndarray, b: float) -> bool:
    """Would any TRAINING nonsense question answer on keywords alone (no cosine,
    no cross-encoder; with and without the question profile)?"""
    from dataclasses import replace

    from superlocalmemory.retrieval import support_gate
    from superlocalmemory.retrieval.support_recipe import FEATURES

    trial = replace(recipe, hub_corrected_unmeasured=value)
    for r in train:
        if r["set"] != "nonsense":
            continue
        for profile in (r["query_evidence"].get("nonsense_similarity"), None):
            qe = {**r["query_evidence"], "nonsense_similarity": profile}
            for c in r.get("candidates", ()):
                if not c.get("bm25_raw"):
                    continue
                ev = {**c, "cosine": None, "hub_corrected": None, "ce_raw": None}
                f = support_gate.features(ev, qe, trial)
                if float(np.array([f[n] for n in FEATURES]) @ coef + b) >= threshold_logit:
                    return True
    return False


def unmeasured_hub(train: list[dict], recipe, coef: np.ndarray, b: float,
                   threshold_logit: float) -> float:
    """``hub_corrected_unmeasured``: the largest grid value at which no training
    nonsense question answers on keywords alone, minus ``VECTORLESS_MARGIN``."""
    safe = None
    for value in _VECTORLESS_GRID:
        if _keyword_only_answers(train, recipe, value, threshold_logit, coef, b):
            break
        safe = value
    return round(max(0.0, (safe or 0.0) - VECTORLESS_MARGIN), 2)


# -- the fit -------------------------------------------------------------------------

def _bounded_logistic(X: np.ndarray, y: np.ndarray, w: np.ndarray, names) -> tuple:
    from scipy.optimize import minimize

    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    pos = (y == 1)
    w = w * len(y) / w.sum()
    w = np.where(pos, w * (~pos).sum() / max(1, pos.sum()), w)

    def loss(p):
        coef, b = p[:-1], p[-1]
        z = Z @ coef + b
        nll = np.logaddexp(0, -z) * y + np.logaddexp(0, z) * (1 - y)
        g = (1 / (1 + np.exp(-z)) - y) * w
        value = float((nll * w).sum() / len(y) + L2 * coef @ coef)
        grad = np.append(Z.T @ g / len(y) + 2 * L2 * coef, g.sum() / len(y))
        return value, grad

    # Bounds live in the standardised space: a raw-scale floor times the sd.
    bounds = [((MIN_WEIGHTS.get(n, 0.0) * s, None) if SIGNS[n] > 0 else (None, 0))
              for n, s in zip(names, sd)] + [(None, None)]
    res = minimize(loss, np.zeros(Z.shape[1] + 1), jac=True, method="L-BFGS-B",
                   bounds=bounds)
    coef = res.x[:-1] / sd
    return coef, float(res.x[-1] - (res.x[:-1] * mu / sd).sum())


def fit(train: list[dict], recipe) -> tuple[np.ndarray, float]:
    from superlocalmemory.retrieval.support_recipe import FEATURES

    vecs = {r["qid"]: _vectors(r, recipe) for r in train}
    coef, b = np.zeros(len(FEATURES)), 0.0
    for _ in range(ROUNDS):
        examples: list[tuple[np.ndarray, int, float]] = []
        for r in train:
            cands = vecs[r["qid"]]
            if not cands:
                continue
            if r["set"] == "real":
                pos = [v for c, v in cands if c.get("key") in r["expected"]]
                if not answered_today(r) or not pos:
                    continue
                examples.append((max(pos, key=lambda v: v @ coef), 1, 1.0))
                continue
            top = sorted((v for _, v in cands), key=lambda v: -(v @ coef))
            top = top[:NEGATIVES_PER_QUESTION]
            weight = (1.0 if r["set"] == "nonsense" else UNANSWERABLE_WEIGHT) / len(top)
            examples.extend((v, 0, weight) for v in top)
        X, y, w = (np.array(col) for col in zip(*examples))
        coef, b = _bounded_logistic(X, y, w, FEATURES)
    return coef, b


def question_logit(row: dict, recipe, coef: np.ndarray, b: float,
                   slice_name: str | None = None) -> float | None:
    scores = [float(v @ coef + b) for c, v in _vectors(row, recipe)
              if slice_name is None or c.get("slice") == slice_name]
    return max(scores) if scores else None


# -- the threshold and the table ---------------------------------------------------------

def choose_threshold(held: list[dict], recipe, coef, b, *,
                     dev: list[dict] | None = None) -> dict:
    """The widest-margin point between the highest held-out nonsense question and
    the lowest real question recall answers today -- on the real side every
    development question counts (``dev``; a real question in the training part
    is still one that must not be lost). When no such point exists the
    threshold keeps every real question (``feasible`` False) and the curve
    below says what each lower real question costs."""
    nonsense = [question_logit(r, recipe, coef, b) for r in held if r["set"] == "nonsense"]
    real = [question_logit(r, recipe, coef, b) for r in (dev or held)
            if r["set"] == "real" and answered_today(r)]
    hi_nonsense = max((z for z in nonsense if z is not None), default=-math.inf)
    lo_real = min((z for z in real if z is not None), default=math.inf)
    feasible = hi_nonsense < lo_real
    z = (hi_nonsense + lo_real) / 2 if feasible else lo_real - REAL_MARGIN
    return {"feasible": feasible, "logit": z, "nonsense_max": hi_nonsense, "real_min": lo_real,
            "probability": 1 / (1 + math.exp(-z))}


def real_floor_curve(dev: list[dict], recipe, coef, b, k: int = 8) -> list[dict]:
    """For the ``k`` lowest real questions: a threshold just below each, and the
    nonsense and no-answer questions it lets through (development half only)."""
    real = sorted((question_logit(r, recipe, coef, b), r["qid"]) for r in dev
                  if r["set"] == "real" and answered_today(r)
                  and question_logit(r, recipe, coef, b) is not None)
    out = []
    for z, qid in real[:k]:
        t = z - 1e-6
        nonsense = [r["qid"] for r in dev if r["set"] == "nonsense"
                    and (question_logit(r, recipe, coef, b) or -math.inf) >= t]
        unans = [r for r in dev if r["set"] == "unanswerable"]
        out.append({"keeps_down_to": qid, "logit": round(z, 3),
                    "real_lost": [q for _, q in real if _ < z][:10],
                    "nonsense_answered": len(nonsense),
                    "nonsense_n": sum(r["set"] == "nonsense" for r in dev),
                    "nonsense_answered_ids": nonsense[:25],
                    "unanswerable_abstain": sum(
                        (question_logit(r, recipe, coef, b) or -math.inf) < t for r in unans),
                    "unanswerable_n": len(unans)})
    return out


def table(rows: list[dict], recipe, coef, b, z: float) -> dict:
    out = {}
    for name in (None, *SLICES):
        real = [r for r in rows if r["set"] == "real" and answered_today(r)
                and (name is None or r.get("slice") == name)]
        nonsense = [r for r in rows if r["set"] == "nonsense"]
        unans = [r for r in rows if r["set"] == "unanswerable"]

        def abstains(r, sl=name):
            q = question_logit(r, recipe, coef, b, sl)
            return q is None or q < z
        out[name or "all"] = {
            "real_kept": f"{sum(not abstains(r, None) for r in real)}/{len(real)}",
            "nonsense_abstain": f"{sum(abstains(r) for r in nonsense)}/{len(nonsense)}",
            "unanswerable_abstain": f"{sum(abstains(r) for r in unans)}/{len(unans)}",
            "real_lost": [r["qid"] for r in real if abstains(r, None)],
            "nonsense_answered": [r["qid"] for r in nonsense if not abstains(r)],
        }
    return out


def curve(held: list[dict], recipe, coef, b) -> list[dict]:
    """Every distinct held-out question logit as a threshold: what it costs."""
    marks = sorted({q for r in held if (q := question_logit(r, recipe, coef, b)) is not None})
    points = []
    for z in marks:
        t = table(held, recipe, coef, b, z)["all"]
        points.append({"logit": round(z, 3), **{k: t[k] for k in (
            "real_kept", "nonsense_abstain", "unanswerable_abstain")}})
    return points


def gate_table(rows: list[dict], slice_of: dict[str, str]) -> dict:
    """What recall actually did (the harness's gate pass), per slice: real
    questions kept in the top 5, nonsense and no-answer questions abstained.
    A nonsense question counts against a slice when it returned any memory
    from that slice (the harness's own reading)."""
    out = {}
    for name in (None, *SLICES):
        real = [r for r in rows if r["set"] == "real"
                and (name is None or r.get("slice") == name)]
        nonsense = [r for r in rows if r["set"] == "nonsense"]
        unans = [r for r in rows if r["set"] == "unanswerable"]

        def answered(r, sl=name):
            keys = r.get("result_keys") or ()
            return bool(keys) if sl is None else any(slice_of.get(k) == sl for k in keys)
        out[name or "all"] = {
            "real_top5": f"{sum(hit_now(r) for r in real)}/{len(real)}",
            "nonsense_abstain": f"{sum(not answered(r) for r in nonsense)}/{len(nonsense)}",
            "unanswerable_abstain": f"{sum(r['abstained'] for r in unans)}/{len(unans)}",
            "real_missed": [r["qid"] for r in real if not hit_now(r)],
            "real_lost_vs_today": [r["qid"] for r in real
                                   if "today" in r and r["today"] and not hit_now(r)],
            "nonsense_answered": [r["qid"] for r in nonsense if answered(r)],
        }
    return out


def _gate_report(rows: list[dict]) -> dict:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "answerability_corpus", _HERE / "answerability_corpus.py")
    corpus = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("answerability_corpus", corpus)
    spec.loader.exec_module(corpus)
    slice_of = corpus.slice_of_key()
    dev = [r for r in rows if not is_fresh(r)]
    fresh = [r for r in rows if is_fresh(r)]
    _, held = split(dev)
    out = {"held_out": gate_table(held, slice_of), "dev_all": gate_table(dev, slice_of)}
    if fresh:
        out["fresh"] = gate_table(fresh, slice_of)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rows", required=True)
    p.add_argument("--profile", default="a")
    p.add_argument("--out")
    p.add_argument("--gate", action="store_true",
                   help="report what recall did (the gate pass) on the held-out split")
    p.add_argument("--baseline", help="rows of recall as shipped before (defines 'today')")
    args = p.parse_args(argv)
    _bootstrap()
    from dataclasses import replace

    from superlocalmemory.retrieval import support_recipe as sr

    rows = [json.loads(line) for line in Path(args.rows).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    rows = [r for r in rows if r.get("profile", args.profile) == args.profile
            and "candidates" in r]
    if not rows:
        print("answerability-fit: no feature rows for that profile", file=sys.stderr)
        return 2
    if args.baseline:
        today = {r["qid"]: hit_now(r) for r in (
            json.loads(line) for line in Path(args.baseline).read_text(
                encoding="utf-8").splitlines() if line.strip())
            if r.get("profile", args.profile) == args.profile and r["set"] == "real"}
        rows = [{**r, "today": today[r["qid"]]} if r["qid"] in today else r for r in rows]
    if args.gate:
        print(json.dumps(_gate_report(rows), indent=2))
        return 0
    # The frozen test half is never fitted, never used for the threshold and
    # never scored here: run recall with the recipe and use --gate for it.
    rows = [r for r in rows if not is_fresh(r)]
    train, held = split(rows)
    recipe = replace(sr.ACTIVE, **imputations(train))
    coef, b = fit(train, recipe)
    chosen = choose_threshold(held, recipe, coef, b, dev=rows)
    unmeasured = unmeasured_hub(train, recipe, coef, b, chosen["logit"])
    report = {
        "split": SPLIT_VERSION, "train": len(train), "held_out": len(held),
        "imputations": {**imputations(train), "hub_corrected_unmeasured": unmeasured},
        "weights": {n: round(float(c), 4) for n, c in zip(sr.FEATURES, coef)},
        "intercept": round(b, 4), "threshold": chosen,
        "held_out_table": table(held, recipe, coef, b, chosen["logit"]),
        "train_table": table(train, recipe, coef, b, chosen["logit"]),
        "all_table": table(rows, recipe, coef, b, chosen["logit"]),
        "held_out_curve": curve(held, recipe, coef, b),
        "dev_real_floor_curve": real_floor_curve(rows, recipe, coef, b),
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if chosen["feasible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
