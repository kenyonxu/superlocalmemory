# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The recipe fit splits once, deterministically, and never fits on held-out rows."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _fit():
    name = "answerability_fit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "scripts" / "quality" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _rows():
    return [{"qid": f"{s}-{c}-{i}", "set": s, "category": c}
            for s, c in (("real", "name"), ("real", "code"), ("nonsense", "letters"))
            for i in range(10)]


def test_split_is_deterministic_disjoint_and_stratified() -> None:
    fit = _fit()
    train, held = fit.split(_rows())
    again, _ = fit.split(list(reversed(_rows())))
    assert sorted(r["qid"] for r in train) == sorted(r["qid"] for r in again)
    assert not {r["qid"] for r in train} & {r["qid"] for r in held}
    for cat in ("name", "code", "letters"):
        assert sum(r["category"] == cat for r in train) == 6
        assert sum(r["category"] == cat for r in held) == 4


def test_every_fitted_weight_keeps_its_declared_sign() -> None:
    from superlocalmemory.retrieval import support_recipe as sr

    fit = _fit()
    assert set(fit.SIGNS) == set(sr.FEATURES)
    for name, weight in sr.ACTIVE.weights.items():
        assert weight * fit.SIGNS[name] >= 0, name


def test_gate_table_reads_what_recall_did_per_slice() -> None:
    fit = _fit()
    rows = [
        {"qid": "r1", "set": "real", "slice": "legacy", "rank": 2, "abstained": False,
         "result_keys": ["a"]},
        {"qid": "r2", "set": "real", "slice": "described", "rank": None, "abstained": True,
         "result_keys": []},
        {"qid": "n1", "set": "nonsense", "rank": None, "abstained": False,
         "result_keys": ["stub"]},
        {"qid": "u1", "set": "unanswerable", "rank": None, "abstained": True,
         "result_keys": []},
    ]
    table = fit.gate_table(rows, {"a": "legacy", "stub": "described"})
    assert table["all"]["real_top5"] == "1/2" and table["all"]["nonsense_abstain"] == "0/1"
    assert table["legacy"]["nonsense_abstain"] == "1/1"  # the stub is not legacy
    assert table["described"]["nonsense_answered"] == ["n1"]
    assert table["all"]["unanswerable_abstain"] == "1/1"
