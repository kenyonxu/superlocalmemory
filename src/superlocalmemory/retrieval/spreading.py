# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The spreading-activation walk. One implementation, no storage in it.

A pure function of an :class:`~.graph_adjacency.AdjacencySnapshot`. It opens no
connection and knows no backend, which is the point: the same walk answers
whether the graph came out of SQLite or CozoDB, so the two can never disagree and
there is nothing to shadow-compare.

WHAT IT COMPUTES
----------------
Activation starts at 1.0 on every fact linked to a query's entities and spreads
outward for ``max_hops``, multiplied each hop by ``decay``, by the edge weight,
and by a PageRank factor on the receiving fact. A fact's score is the best path
that reaches it — a max, not a sum, so a fact reached twice is not thereby more
relevant. Two enrichments follow the spread (a community bonus and a
contradiction penalty), then scores are normalised to [0, 1].

SYNCHRONOUS, AND WHY THAT IS A FIX
----------------------------------
Each hop is computed from the previous hop's values only. The dict-based walk
this replaces read ``activation[fid]`` while iterating the frontier, so a fact
updated earlier in a hop propagated again within that same hop — meaning the
walk could reach further than ``max_hops`` allowed, by an amount that depended
on set iteration order.

That is benign exactly while ``decay * weight * pagerank_boost < 1`` everywhere,
because then the max-product has a unique fixpoint and order only changes how
fast it is reached.

**It is not benign in general, and it is not benign here.** The boost is capped
at 2.0 and ``decay`` is 0.7, so any graph whose peak rank reaches 0.215
amplifies. Measured across four real workspaces after the ranking was repaired:

    workspace          facts    peak rank   peak factor
    a large one       12,078     0.003984        0.7056
    another one        4,038     0.007155        0.7100
    a small one           24     0.106919        0.8497
    a very small one       5     0.290068        1.1061   ← amplifies

The last row is the point. A small graph concentrates rank by construction — a
five-fact workspace on a first day of use is not a corner case, it is every
workspace's first day — and there the old walk's answer depended on dictionary
ordering. A bounded synchronous iteration is well-defined in both regimes.

(These numbers replace an earlier note citing a peak of 0.1 and a factor of
0.84. That reading came from a ranking table whose scores summed to 1.9999 and
3.3150 on two real stores rather than to 1, so the peak it reported was an
artefact of the table being wrong, not a property of any graph.)
:meth:`AdjacencySnapshot.peak_propagation_factor` is how a caller can see which
regime a store is in.

WHY ARRAYS
----------
The relaxation is a max-times sparse product. Held as CSR, one hop is one
multiply and one segment-maximum over the edge array, in place of a Python loop
that did 3.4 million dictionary lookups to produce a hundred numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from superlocalmemory.retrieval.graph_adjacency import (
    PAGERANK_BOOST_CAP,
    AdjacencySnapshot,
)

logger = logging.getLogger(__name__)

#: Community-bonus ceiling and the penalty for a fact outside every seed
#: community. Carried over unchanged from the walk that introduced them.
COMMUNITY_BONUS_SCALE = 0.15
COMMUNITY_BONUS_CAP = 1.3
COMMUNITY_OUTSIDER_PENALTY = 0.9


@dataclass(frozen=True)
class ActivationResult:
    """Activation over the snapshot's node space, plus how it was reached."""

    scores: np.ndarray
    hops_run: int
    seeded: int
    normalised_by: float

    def as_mapping(
        self, snapshot: AdjacencySnapshot, *, threshold: float
    ) -> dict[str, float]:
        """The scores at or above ``threshold``, keyed by fact id."""
        keep = np.flatnonzero(self.scores >= threshold)
        return {snapshot.node_ids[i]: float(self.scores[i]) for i in keep}


def _boost(snapshot: AdjacencySnapshot) -> np.ndarray:
    """Per-node PageRank multiplier, or ones when no metrics were measured.

    Without metrics the walk deliberately drops the edge weight too. That is not
    an oversight: weighting without a compensating boost dampens propagation by
    about 14% and measurably lowered retrieval quality, so the two arrived
    together and have to leave together.
    """
    if not snapshot.has_metrics:
        return np.ones(snapshot.node_count, dtype=np.float64)
    return np.minimum(1.0 + snapshot.pagerank * 2.0, PAGERANK_BOOST_CAP)


def _segment_max(values: np.ndarray, indptr: np.ndarray, rows: int) -> np.ndarray:
    """Maximum of ``values`` within each CSR row; 0.0 for an empty row.

    ``np.maximum.reduceat`` returns the element at the start offset for a
    zero-length segment rather than an identity, so empty rows would inherit
    whichever neighbour happened to sit at that offset — a silent wrong answer
    for exactly the isolated facts a graph channel should score at zero.
    """
    out = np.zeros(rows, dtype=np.float64)
    if values.size == 0:
        return out
    starts = indptr[:-1]
    non_empty = starts < indptr[1:]
    if not non_empty.any():
        return out
    reduced = np.maximum.reduceat(values, starts[non_empty])
    out[non_empty] = reduced
    return out


def activate(
    snapshot: AdjacencySnapshot,
    seed_entity_ids: Sequence[str],
    *,
    decay: float,
    threshold: float,
    max_hops: int,
) -> ActivationResult:
    """Spread activation from a query's entities across the fact graph."""
    n = snapshot.node_count
    scores = np.zeros(n, dtype=np.float64)
    if n == 0 or not seed_entity_ids:
        return ActivationResult(scores, 0, 0, 1.0)

    seeded_nodes: list[int] = []
    for entity_id in seed_entity_ids:
        seeded_nodes.extend(snapshot.entity_to_facts.get(entity_id, ()))
    if seeded_nodes:
        scores[np.asarray(seeded_nodes, dtype=np.int64)] = 1.0

    visited_entities = set(seed_entity_ids)
    frontier = np.flatnonzero(scores > 0.0)
    boost = _boost(snapshot)
    use_weights = snapshot.has_metrics
    hops_run = 0

    for hop in range(1, max_hops):
        hop_decay = decay**hop
        if hop_decay < threshold:
            break
        if frontier.size == 0:
            break
        hops_run = hop

        # --- edge propagation, one vectorised pass over the edge array -------
        # Every edge sits in both endpoints' rows with the same weight, so the
        # maximum over a row is the maximum over that node's incoming values.
        contributions = scores[snapshot.indices] * decay
        if use_weights:
            contributions = contributions * snapshot.weights
        incoming = _segment_max(contributions, snapshot.indptr, n)
        if use_weights:
            incoming = incoming * boost
        # A hop only ever raises a score, and only above the threshold.
        candidate = np.where(incoming >= threshold, incoming, 0.0)
        improved = candidate > scores
        next_nodes = np.flatnonzero(improved)
        scores[improved] = candidate[improved]

        # --- entity hop: facts reached through a newly seen entity ----------
        # Stateful and cheap, so it stays a loop. It reads the frontier as it
        # was at the start of this hop, which is the same order of operations
        # the dict walk used.
        newly_seen: list[str] = []
        for node in frontier.tolist():
            for entity_id in snapshot.fact_to_entities[node]:
                if entity_id not in visited_entities:
                    visited_entities.add(entity_id)
                    newly_seen.append(entity_id)
        entity_nodes: list[int] = []
        for entity_id in newly_seen:
            entity_nodes.extend(snapshot.entity_to_facts.get(entity_id, ()))
        if entity_nodes:
            reached = np.asarray(sorted(set(entity_nodes)), dtype=np.int64)
            lifts = reached[hop_decay > scores[reached]]
            if lifts.size:
                scores[lifts] = hop_decay
                next_nodes = np.union1d(next_nodes, lifts)

        frontier = next_nodes

    return ActivationResult(scores, hops_run, len(seeded_nodes), 1.0)


def apply_community_bias(
    scores: np.ndarray,
    snapshot: AdjacencySnapshot,
    seed_entity_ids: Iterable[str],
    *,
    penalise_outsiders: bool = True,
) -> None:
    """Favour facts sharing a community with the query's seeds. In place.

    A no-op without metrics, which is also when ``community`` is all -1.

    ``penalise_outsiders`` exists because the two callers genuinely differ and
    the difference is not cosmetic. Search damps a fact belonging to no seed
    community by 0.9; candidate scoring does not, because it re-scores a set
    another channel already chose and a graph signal has no business vetoing
    that channel's find. Collapsing the two into one behaviour would silently
    change one caller's results, so the asymmetry is a parameter and this
    paragraph is why.
    """
    if not snapshot.has_metrics or scores.size == 0:
        return
    seed_nodes: list[int] = []
    for entity_id in seed_entity_ids:
        seed_nodes.extend(snapshot.entity_to_facts.get(entity_id, ()))
    if not seed_nodes:
        return
    seed_communities = snapshot.community[np.asarray(seed_nodes, dtype=np.int64)]
    seed_communities = seed_communities[seed_communities >= 0]
    if seed_communities.size == 0:
        return
    labels, counts = np.unique(seed_communities, return_counts=True)
    total = float(counts.sum())
    share = dict(zip(labels.tolist(), (counts / total).tolist()))

    known = snapshot.community >= 0
    multiplier = np.ones(scores.size, dtype=np.float64)
    for label, fraction in share.items():
        in_seed = known & (snapshot.community == label)
        multiplier[in_seed] = min(
            1.0 + COMMUNITY_BONUS_SCALE * fraction, COMMUNITY_BONUS_CAP
        )
    if penalise_outsiders:
        outsider = known & ~np.isin(snapshot.community, labels)
        multiplier[outsider] = COMMUNITY_OUTSIDER_PENALTY
    scores *= multiplier


def normalise(scores: np.ndarray, *, threshold: float) -> float:
    """Scale scores so the best is 1.0. Returns the divisor used. In place.

    Reported rather than hidden because it is the only reason a fact seeded at
    exactly 1.0 can come back as something else, which is otherwise a confusing
    thing to see in a trace.
    """
    if scores.size == 0:
        return 1.0
    kept = scores[scores >= threshold]
    if kept.size == 0:
        return 1.0
    peak = float(kept.max())
    if peak > 0.0:
        scores /= peak
        return peak
    return 1.0


def ranked(
    scores: np.ndarray, snapshot: AdjacencySnapshot, *, threshold: float
) -> list[tuple[str, float]]:
    """Facts at or above ``threshold``, best first, ties broken on fact id.

    The tie-break is part of the contract, not a detail. An entity-seeded walk
    puts every directly-linked fact at exactly the same score, so a large group
    arrives at the cut-off together and which of them survives ``top_k`` is
    decided entirely by how ties are ordered. Two implementations that agreed on
    every score but not on this returned different results for the same query.
    """
    keep = np.flatnonzero(scores >= threshold)
    pairs = [(snapshot.node_ids[i], float(scores[i])) for i in keep]
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return pairs
