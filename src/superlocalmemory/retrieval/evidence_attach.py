# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Put each recalled memory's evidence features on it (``evidence_features``).

Reads what recall already has -- the question's vector (cached for this
question), the stored hub baselines and the cross-encoder's raw scores -- plus
the probe vectors when this process has embedded them (``hub_baseline.PROBES``;
never embedded here). A memory the background pass has not reached yet gets its
hub baseline measured on the spot from its own stored vector, so a store that
is still filling in is not judged on raw cosine.

Fail-open: a feature that cannot be computed is recorded as missing, and an
error never reaches the caller. No score or order changes here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from superlocalmemory.retrieval import evidence_features as ef
from superlocalmemory.retrieval import hub_baseline

logger = logging.getLogger(__name__)


def attach(
    query: str,
    results: Sequence[Any],
    *,
    query_vector: Sequence[float] | None,
    ce_scores: Mapping[str, tuple[float, float]],
    stored_baselines: Callable[[list[str]], Mapping[str, tuple[float, float]]],
    probes: np.ndarray | None,
    terms_in_store: Callable[[list[str]], set[str]] | None = None,
) -> dict:
    """Record every result's features; return the question's own."""
    try:
        terms = ef.query_terms(query)
        rare = ef.rare_terms(terms, ef.model_vocabulary())
        q_evidence = ef.query_evidence(
            query, terms, rare=rare,
            nonsense_similarity=hub_baseline.query_profile(query_vector, probes),
            orphan_terms=_orphans(terms, terms_in_store))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("query evidence: %s", exc)
        return {}
    q_evidence["hub_probes_ready"] = probes is not None
    if not results:
        return q_evidence
    try:
        stored = stored_baselines([r.fact.fact_id for r in results])
        for r in results:
            raw, norm = ce_scores.get(r.fact.fact_id, (None, None))
            topk, mean = _baseline(r.fact, stored, probes)
            source = ("stored" if r.fact.fact_id in stored
                      else "measured" if topk is not None else "missing")
            r.evidence = ef.build(
                query=query, terms=terms, query_vector=query_vector, fact=r.fact,
                channel_scores=r.channel_scores, ce_raw=raw, ce_batch_norm=norm,
                hub_baseline=topk, hub_baseline_mean=mean, hub_baseline_source=source,
                rare=rare,
            )
        # How many of these memories the gate had to impute a baseline for, so a
        # caller can tell "no match" from "nothing measured in this space yet".
        q_evidence["hub_baselines_missing"] = sum(
            1 for r in results if r.evidence.get("hub_baseline_source") == "missing")
    except Exception as exc:
        logger.debug("result evidence: %s", exc)
    return q_evidence


def _orphans(terms: Sequence[str],
             terms_in_store: Callable[[list[str]], set[str]] | None) -> float | None:
    countable = ef.countable_terms(terms)
    if not countable or terms_in_store is None:
        return None
    try:
        return ef.orphan_share(countable, terms_in_store(countable))
    except Exception as exc:  # no keyword index: unmeasured, never an error
        logger.debug("orphan terms: %s", exc)
        return None


def floor_hub_corrected(
    candidates: Sequence[Any], facts: Mapping[str, Any], min_semantic: float, *,
    stored: Callable[[list[str]], Mapping[str, tuple[float, float]]],
    probes: np.ndarray | None,
) -> dict[str, float]:
    """``fact_id -> cosine - hub baseline`` for the candidates the raw floor would
    drop on meaning alone (a semantic score under the floor and no keyword,
    entity or time evidence): the only ones the hub door can change. One read
    of stored baselines; a missing one is measured from the stored vector."""
    from superlocalmemory.retrieval.time_window import (
        has_primary_evidence,
        semantic_floor_on_channel_scale,
    )

    floor = semantic_floor_on_channel_scale(min_semantic)
    near = [c for c in candidates
            if 0.0 < (getattr(c, "channel_scores", None) or {}).get("semantic", 0.0) < floor
            and not has_primary_evidence(c.channel_scores, min_semantic, min_media=2.0)]
    if not near:
        return {}
    try:
        found = stored([c.fact_id for c in near])
    except Exception as exc:  # fail-open: the raw floor alone, as before
        logger.debug("floor hub baselines: %s", exc)
        return {}
    out: dict[str, float] = {}
    for c in near:
        fact = facts.get(c.fact_id)
        topk, _ = _baseline(fact, found, probes) if fact is not None else (None, None)
        if topk is None and c.fact_id in found:
            topk = found[c.fact_id][0]
        if topk is not None:
            out[c.fact_id] = 2.0 * c.channel_scores["semantic"] - 1.0 - topk
    return out


def _baseline(fact: Any, stored: Mapping[str, tuple[float, float]],
              probes: np.ndarray | None) -> tuple[float | None, float | None]:
    found = stored.get(fact.fact_id)
    if found is not None:
        return found
    measured = hub_baseline.baseline_of(getattr(fact, "embedding", None), probes)
    return measured if measured is not None else (None, None)


__all__ = ["attach", "floor_hub_corrected"]
