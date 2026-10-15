# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A recall says which memories were judged without a hub baseline.

When a space has no baselines the answer-support gate imputes a constant fitted
on one model (`support_recipe.hub_missing_baseline`). The evidence now records,
per memory and for the question, that this happened, so a caller can tell "no
match" from "nothing measured in this space yet". No scoring rule changes.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from superlocalmemory.retrieval import evidence_attach
from superlocalmemory.retrieval.hub_baseline import baselines

DIM = 6


def _unit(seed: int) -> list[float]:
    v = np.random.default_rng(seed).standard_normal(DIM)
    return (v / np.linalg.norm(v)).tolist()


def _result(fact_id: str, embedding):
    fact = SimpleNamespace(fact_id=fact_id, embedding=embedding, content="alice approved it",
                           canonical_entities=[], entities=[])
    return SimpleNamespace(fact=fact, channel_scores={"semantic": 0.8}, evidence=None)


def _attach(results, stored, probes):
    return evidence_attach.attach(
        "who approved it", results, query_vector=_unit(1), ce_scores={},
        stored_baselines=lambda ids: stored, probes=probes)


def test_stored_measured_and_missing_baselines_are_told_apart():
    probes = np.asarray([_unit(10 + i) for i in range(4)], dtype=np.float32)
    mean, topk = baselines([_unit(2)], probes)[0]
    results = [_result("stored", _unit(2)), _result("measured", _unit(3)),
               _result("novector", None)]
    qe = _attach(results, {"stored": (topk, mean)}, probes)
    source = {r.fact.fact_id: r.evidence["hub_baseline_source"] for r in results}
    assert source == {"stored": "stored", "measured": "measured", "novector": "missing"}
    assert qe["hub_baselines_missing"] == 1 and qe["hub_probes_ready"] is True


def test_a_cold_space_reports_every_memory_as_missing_and_probes_not_ready():
    results = [_result("a", _unit(2)), _result("b", _unit(3))]
    qe = _attach(results, {}, None)
    assert [r.evidence["hub_baseline_source"] for r in results] == ["missing", "missing"]
    assert qe["hub_baselines_missing"] == 2 and qe["hub_probes_ready"] is False
