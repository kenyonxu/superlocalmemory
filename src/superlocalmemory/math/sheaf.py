# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Experimental pairwise sheaf residuals with factual-conflict admission.

This computes local residuals, not first cohomology. A residual is reported
as a contradiction only after explicit subject/attribute incompatibility.

V1 applied sheaf at RECALL (HARMFUL — penalized diverse multi-hop results).
V2 runs at ENCODING ONLY, checking new facts against graph-connected facts.

Coboundary: delta_0 f(e) = R_b emb_b - R_a emb_a
Restriction maps by edge type: ENTITY=I, TEMPORAL=s*I (s<1), SEMANTIC=I.
Severity = ||delta_0 f(e)|| / (||R_a emb_a|| + ||R_b emb_b||).

Refs: Curry 2014 (arXiv:1303.3255), Hansen & Ghrist 2019, Robinson 2020.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from superlocalmemory.storage.embedding_codec import decode_embedding

if TYPE_CHECKING:
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact

logger = logging.getLogger(__name__)

_EPS = 1e-12

# Temporal tolerance: fraction of coboundary norm below which temporal
# edges are NOT flagged as contradictions (legitimate state changes).
TEMPORAL_TOLERANCE: float = 0.15


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContradictionResult:
    """Detected contradiction between two facts."""

    fact_id_a: str   # New fact being stored
    fact_id_b: str   # Existing fact it conflicts with
    severity: float  # Normalized disagreement [0.0, 1.0]
    edge_type: str   # Graph edge type (entity/temporal/semantic/causal)
    description: str


# ---------------------------------------------------------------------------
# Restriction maps — non-trivial (V1 used identity everywhere)
# ---------------------------------------------------------------------------

def _restriction_for_edge_type(
    edge_type: str,
    dim: int,
    emb_a: np.ndarray | None = None,
    emb_b: np.ndarray | None = None,
) -> np.ndarray:
    """Edge-type-specific restriction map.

    Entity edges use non-trivial maps that AMPLIFY the disagreement
    along the axis of maximum difference between embeddings:
        R = 0.7*I + 0.3 * outer(diff, diff) / ||diff||^2

    This projects contradictions onto the most discriminative subspace,
    making the coboundary norm more sensitive to real disagreements.

    Temporal edges use identity restriction (scalar multiples cancel
    in the normalized coboundary). Temporal tolerance is applied at
    the caller level via TEMPORAL_TOLERANCE threshold.
    """
    if edge_type == "entity" and emb_a is not None and emb_b is not None:
        diff = emb_a - emb_b
        norm_sq = max(float(np.dot(diff, diff)), 1e-12)
        return 0.7 * np.eye(dim) + 0.3 * np.outer(diff, diff) / norm_sq
    return np.eye(dim)


def edge_residual(
    emb_a: np.ndarray, emb_b: np.ndarray,
    R_a: np.ndarray, R_b: np.ndarray,
) -> np.ndarray:
    """Coboundary residual: delta_0 f(e) = R_b emb_b - R_a emb_a."""
    return R_b @ emb_b - R_a @ emb_a


def coboundary_norm(
    emb_a: np.ndarray, emb_b: np.ndarray,
    R_a: np.ndarray, R_b: np.ndarray,
) -> float:
    """Normalized coboundary: ||delta|| / (||R_a emb_a|| + ||R_b emb_b||).

    Returns [0, ~2]. Near 0 = agree; near 1+ = strong disagreement.
    """
    residual = edge_residual(emb_a, emb_b, R_a, R_b)
    res_norm = float(np.linalg.norm(residual))
    denom = float(np.linalg.norm(R_a @ emb_a) + np.linalg.norm(R_b @ emb_b) + _EPS)
    return res_norm / denom


class SheafConsistencyChecker:
    """Detect supported conflicts at ENCODING time with a residual score.

    V1 differences: uses real graph edges (not all-pairs), non-trivial
    restriction maps, and returns results for the pipeline to act on.
    """

    def __init__(
        self,
        db: DatabaseManager,
        contradiction_threshold: float = 0.7,
    ) -> None:
        self._db = db
        self._threshold = max(0.0, min(2.0, contradiction_threshold))

    # -- Public API ---------------------------------------------------------

    def check_consistency(self, new_fact: AtomicFact, profile_id: str) -> list[ContradictionResult]:
        """Check supported logical conflicts along graph edges; topics abstain."""
        if not new_fact.embedding or not new_fact.canonical_entities:
            return []
        contradictions = []
        checked = set()
        for edge in self._db.get_edges_for_node(new_fact.fact_id, profile_id):
            other_id = edge.target_id if edge.source_id == new_fact.fact_id else edge.source_id
            if other_id in checked or edge.edge_type.value in ("contradiction", "supersedes"):
                continue
            checked.add(other_id)
            other = self._db.get_fact(other_id, profile_id)
            if other is None:
                continue
            result = self._pair_conflict(new_fact, other, edge.edge_type.value)
            if result is not None:
                contradictions.append(result)
        return contradictions

    def _pair_conflict(self, left: AtomicFact, right: AtomicFact,
                       edge_type: str) -> ContradictionResult | None:
        from superlocalmemory.math.contradiction_evidence import incompatible

        if not isinstance(right.content, str) or not incompatible(left, right):
            return None
        if left.embedding is None or right.embedding is None:
            return None
        a, b = np.asarray(left.embedding), np.asarray(right.embedding)
        if a.ndim != 1 or a.shape != b.shape or not a.size:
            return None
        restriction = _restriction_for_edge_type(edge_type, a.size, a, b)
        severity = coboundary_norm(a, b, restriction, restriction)
        if severity <= self._threshold:
            return None
        return ContradictionResult(
            left.fact_id, right.fact_id, min(severity, 1.0), edge_type,
            f"Incompatible subject/attribute values; embedding residual {severity:.3f}",
        )

    def detect_contradictions_batch(self, facts: list[AtomicFact],
                                    profile_id: str) -> list[ContradictionResult]:
        """Check explicit incompatible slots within canonical entity groups."""
        entity_groups: dict[str, list[AtomicFact]] = {}
        for fact in facts:
            if fact.embedding is not None:
                for eid in fact.canonical_entities:
                    entity_groups.setdefault(eid, []).append(fact)
        contradictions = []
        checked = set()
        for group in entity_groups.values():
            for index, left in enumerate(group):
                for right in group[index + 1:]:
                    pair = tuple(sorted((left.fact_id, right.fact_id)))
                    if pair in checked:
                        continue
                    checked.add(pair)
                    result = self._pair_conflict(left, right, "entity")
                    if result is not None:
                        contradictions.append(result)
        return contradictions

    # -- Internal -----------------------------------------------------------

    def _get_fact_embedding(self, fact_id: str) -> np.ndarray | None:
        """Load a fact's embedding from the database."""
        rows = self._db.execute(
            "SELECT embedding FROM atomic_facts WHERE fact_id = ?",
            (fact_id,),
        )
        if not rows:
            return None
        raw = dict(rows[0]).get("embedding")
        if raw is None or raw == "":
            return None
        try:
            data = decode_embedding(raw, fact_id=str(fact_id))
        except ValueError:
            return None
        if data is None:
            return None
        return np.asarray(data, dtype=np.float64)
