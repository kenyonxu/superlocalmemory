# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""One shape for the graph the entity walk reads, whoever stored it.

WHY THIS EXISTS
---------------
The graph was stored twice — in SQLite and, once promoted, in CozoDB — and the
*walk over it* was written twice with it. The two implementations did not compute
the same function: the SQLite one multiplies activation by a PageRank factor at
every hop and the Cozo one had no PageRank at all. Measured on a copy of the
author's store, 3,567 of 3,667 shared facts came out with different scores, the
top-20 sets differed, and the projected path therefore failed its shadow
comparison on **every** query and fell back to SQLite. The projection was correct
data that nothing could use.

The lesson is not "fix the second walk". It is that a storage backend must supply
**data, not behaviour**. So this module defines the one shape the walk consumes,
and each store gets an adapter that produces it. Adding a third store later is
one adapter and no algorithm; there is no second implementation to keep in step,
and nothing to shadow-compare, because there is only one answer to compare.

WHY IT IS ARRAYS AND NOT DICTS
------------------------------
The walk's cost was never the storage engine or the size of the graph — 7,460
nodes over 849k edges is small. It was doing 3.4 million relaxations as
interpreted dictionary lookups (cProfile counted 4,367,932 ``dict.get`` calls in
one recall). Held as CSR arrays the same relaxation is a handful of vectorised
passes. See :mod:`superlocalmemory.retrieval.spreading`.

The node space is fact ids. Entity ids are deliberately a separate namespace:
treating them as graph nodes produces a healthy-looking and semantically wrong
graph, which is why the projection keeps ``fact_entity`` as an explicit bridge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

import numpy as np

logger = logging.getLogger(__name__)

#: Cap on the PageRank multiplier, matching the walk that defined it.
PAGERANK_BOOST_CAP = 2.0


@dataclass(frozen=True)
class AdjacencySnapshot:
    """An immutable view of one profile's fact graph, ready to walk.

    Frozen because the walk must not be able to change the graph underneath a
    concurrent reader — the channel serialises *loading* behind a lock, and a
    snapshot handed out after that is safe to read from anywhere.
    """

    node_ids: tuple[str, ...]
    node_index: Mapping[str, int]
    #: CSR over an UNDIRECTED graph: every edge appears in both endpoints' rows
    #: with the same weight, so "incoming to j" and "outgoing from j" are the
    #: same list. The walk relies on that to take a segment maximum per row.
    indptr: np.ndarray
    indices: np.ndarray
    weights: np.ndarray
    entity_to_facts: Mapping[str, tuple[int, ...]]
    fact_to_entities: tuple[tuple[str, ...], ...]
    #: Per-node PageRank and community, dense so the walk never branches on
    #: presence. 0.0 and -1 mean "not measured", which is what the dict-based
    #: walk expressed as a missing key.
    pagerank: np.ndarray
    community: np.ndarray
    has_metrics: bool
    #: Provenance, so a surface can say which store answered and an operator can
    #: tell a fast path from a fallback rather than guessing.
    source: str = "sqlite"
    edge_count: int = 0
    fact_count: int = 0
    profile_id: str = "default"
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.node_ids)

    def index_of(self, fact_id: str) -> int | None:
        return self.node_index.get(fact_id)

    def peak_propagation_factor(self, decay: float) -> float:
        """Largest ``decay * weight * pagerank_boost`` any edge can apply.

        At or above 1.0 the walk can amplify along a path, and a bounded
        iteration is then the only well-defined reading of it — which is what
        :mod:`spreading` implements, and why it does not matter that the
        dict-based walk was order-dependent in that regime.

        Measured across four real workspaces: 0.7056 and 0.7100 on the two
        large ones, 0.8497 on a 24-fact one, and **1.1061 on a five-fact one**,
        which amplifies. Small graphs concentrate rank by construction, so the
        amplifying regime is not exotic — it is what every workspace looks like
        on its first day.
        """
        if self.weights.size == 0:
            return 0.0
        boost = np.minimum(1.0 + self.pagerank * 2.0, PAGERANK_BOOST_CAP)
        if not self.has_metrics:
            return float(decay)
        return float(np.max(decay * self.weights * boost[self.indices]))


def snapshot_from_maps(
    adjacency: Mapping[str, Sequence[tuple[str, float]]],
    entity_to_facts: Mapping[str, Iterable[str]],
    fact_to_entities: Mapping[str, Iterable[str]],
    graph_metrics: Mapping[str, Mapping[str, Any]] | None,
    *,
    source: str,
    profile_id: str,
    nodes: Iterable[str] | None = None,
    fact_count: int = 0,
) -> AdjacencySnapshot:
    """Build a snapshot from the dict form both adapters produce.

    Node order is the sorted fact ids, not insertion order. That is what makes a
    snapshot reproducible: two loads of the same graph produce the same arrays,
    so a score computed from them is the same number and not a function of which
    row a database happened to return first.

    ``nodes`` IS THE VISIBLE FACT CORPUS, NOT THE FACTS THAT HAVE EDGES.
    An earlier version derived the node space from the adjacency keys, which
    silently excluded every fact with no edge yet — and ingestion is
    queryable-first, so that is exactly the set a user has just added. Those
    facts are reachable through their entities and the walk seeds them at 1.0;
    leaving them out of the node space scored them zero instead. Caught on a real
    store: four candidates, one of them the highest-scoring result for its query.
    An edgeless fact is a node with an empty CSR row, which the walk's
    segment-maximum already reads as zero incoming activation.
    """
    node_ids = tuple(sorted(set(nodes) if nodes is not None else set(adjacency)))
    node_index = {fid: i for i, fid in enumerate(node_ids)}
    n = len(node_ids)

    indptr = np.zeros(n + 1, dtype=np.int64)
    flat_indices: list[int] = []
    flat_weights: list[float] = []
    for i, fid in enumerate(node_ids):
        # Sorted within the row for the same reason the rows are sorted.
        neighbours = sorted(
            (node_index[nid], float(w))
            for nid, w in adjacency.get(fid, ())
            if nid in node_index
        )
        for j, w in neighbours:
            flat_indices.append(j)
            flat_weights.append(w)
        indptr[i + 1] = len(flat_indices)

    indices = np.asarray(flat_indices, dtype=np.int64)
    weights = np.asarray(flat_weights, dtype=np.float64)

    metrics = graph_metrics or {}
    pagerank = np.zeros(n, dtype=np.float64)
    community = np.full(n, -1, dtype=np.int64)
    for fid, i in node_index.items():
        entry = metrics.get(fid)
        if not entry:
            continue
        pagerank[i] = float(entry.get("pagerank_score", 0.0) or 0.0)
        comm = entry.get("community_id")
        if comm is not None:
            try:
                community[i] = int(comm)
            except (TypeError, ValueError):
                pass

    e2f = {
        eid: tuple(sorted(node_index[f] for f in facts if f in node_index))
        for eid, facts in entity_to_facts.items()
    }
    f2e = tuple(
        tuple(fact_to_entities.get(fid, ()) or ()) for fid in node_ids
    )

    return AdjacencySnapshot(
        node_ids=node_ids,
        node_index=node_index,
        indptr=indptr,
        indices=indices,
        weights=weights,
        entity_to_facts=e2f,
        fact_to_entities=f2e,
        pagerank=pagerank,
        community=community,
        has_metrics=bool(metrics),
        source=source,
        edge_count=int(indices.size // 2),
        fact_count=fact_count or n,
        profile_id=profile_id,
    )


class AdjacencySource(Protocol):
    """A store that can hand over one profile's fact graph.

    Data only. A source that also implemented the walk is the defect this
    interface exists to prevent.
    """

    name: str

    def load(
        self,
        profile_id: str,
        *,
        include_global: bool = False,
        include_shared: bool = False,
    ) -> AdjacencySnapshot: ...
