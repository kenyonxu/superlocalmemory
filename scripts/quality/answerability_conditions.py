# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The merge rule: no real answer lost against gate-off, in every condition.

Each condition is a state recall really meets, simulated on the warm engine
and undone afterwards:

``warm``          everything loaded (the default measurement)
``probes_cold``   no nonsense probes and no hub baselines (just after a start,
                  before the baseline pass)
``ce_absent``     no cross-encoder
``keyword_only``  no query vector, no cross-encoder, no probes or baselines

Every condition is asked twice, with the answer-support gate on and off
(``SLM_RECALL_NO_SUPPORT_GATE=1``: recall as it was before the gate). A real
answer is lost when gate-off had its memory in the top five and the gated
recall does not.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

CONDITIONS = ("warm", "probes_cold", "ce_absent", "keyword_only")
TOP_K = 5
_SWITCH = "SLM_RECALL_NO_SUPPORT_GATE"


def _hit(row: dict) -> bool:
    return row.get("rank") is not None and row["rank"] <= TOP_K


def _rate(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def tally(rows: list[dict]) -> dict:
    by = {k: [r for r in rows if r["set"] == k] for k in ("real", "nonsense", "unanswerable")}
    return {
        "real_n": len(by["real"]),
        "real_hits": sum(1 for r in by["real"] if _hit(r)),
        "real_abstained": sum(1 for r in by["real"] if r["abstained"]),
        "nonsense_abstain_rate": _rate(sum(r["abstained"] for r in by["nonsense"]),
                                       len(by["nonsense"])),
        "unanswerable_abstain_rate": _rate(sum(r["abstained"] for r in by["unanswerable"]),
                                           len(by["unanswerable"])),
    }


def compare(gated: list[dict], gate_off: list[dict]) -> dict:
    """One condition: both tallies and the real answers lost or gained."""
    before = {r["qid"]: r for r in gate_off}
    real = [r for r in gated if r["set"] == "real"]
    return {
        "gated": tally(gated),
        "gate_off": tally(gate_off),
        "real_lost": [r["qid"] for r in real if _hit(before[r["qid"]]) and not _hit(r)],
        "real_gained": [r["qid"] for r in real if _hit(r) and not _hit(before[r["qid"]])],
    }


@contextmanager
def gate_off() -> Iterator[None]:
    previous = os.environ.get(_SWITCH)
    os.environ[_SWITCH] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_SWITCH, None)
        else:
            os.environ[_SWITCH] = previous


def _patches(name: str) -> dict[str, Any]:
    no_probes = {"_probe_matrix": lambda: None, "_hub_baselines": lambda *a, **k: {}}
    no_ce = {"_reranker": None}
    if name == "warm":
        return {}
    if name == "probes_cold":
        return no_probes
    if name == "ce_absent":
        return no_ce
    if name == "keyword_only":
        return {**no_probes, **no_ce, "_embed_query": lambda q: (None, None),
                "_query_embedding_cache": {}}
    raise ValueError(f"unknown condition {name!r}; one of {', '.join(CONDITIONS)}")


@contextmanager
def condition(engine: Any, name: str) -> Iterator[None]:
    """``engine`` as it is in condition ``name``; restored on exit."""
    from superlocalmemory.retrieval import hub_baseline

    patches = _patches(name)
    target = engine._retrieval_engine
    saved = {key: target.__dict__.get(key, _MISSING) for key in patches}
    lazy = hub_baseline.lazy_warm
    try:
        for key, value in patches.items():
            setattr(target, key, value)
        if "_probe_matrix" in patches:
            hub_baseline.lazy_warm = lambda embedder: False
        yield
    finally:
        hub_baseline.lazy_warm = lazy
        for key, value in saved.items():
            if value is _MISSING:
                delattr(target, key)
            else:
                setattr(target, key, value)


class _Missing:
    pass


_MISSING = _Missing()

__all__ = ["CONDITIONS", "compare", "condition", "gate_off", "tally"]
