# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Change an entity-graph snapshot by a write's size, not by the store's.

A pure function from (snapshot, delta) to a NEW snapshot: the input is never
mutated, so a reader still walking the old one is unaffected (read-copy-update)
and needs no lock. retrieval/graph_catchup decides what the delta is; this
module only applies it.

The cost is a few vectorised passes over the edge arrays plus a dictionary per
touched entity -- on a 26k-node / 611k-edge graph, single-digit milliseconds
with no removal and ~20 ms with one -- against 2.3-5 s for the full rebuild it
replaces.

WHY THE RESULT EQUALS A FRESH BUILD FOR THE WALK
------------------------------------------------
* Removed facts leave the node space, and every edge row naming them goes with
  them, exactly as a fresh build never loads them.
* New facts are appended at the end instead of in sorted position. The walk
  (retrieval/spreading) is a max-product: a row maximum, a ``>`` comparison and
  ``sorted(set(...))`` over the entity hop, so node and row order cannot change
  a score. Only the arrays' order differs from a fresh build, never a value.
* An edge that arrives twice (a re-insert, or a write caught by two watermarks)
  becomes two identical row entries; a maximum over duplicates is the same
  maximum. The periodic compaction drops them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from superlocalmemory.retrieval.graph_adjacency import AdjacencySnapshot


@dataclass(frozen=True)
class GraphDelta:
    """What changed in the store since a snapshot was taken."""

    #: Facts that may no longer be shown (deleted, withheld, moved out of scope).
    removed: frozenset[str] = frozenset()
    #: Visible facts that are new or changed -> their canonical entity ids.
    facts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Links to add as (source, target, weight); unknown endpoints are skipped.
    edges: Sequence[tuple[str, str, float]] = ()
    #: PageRank and community for facts new to the snapshot, when measured.
    metrics: Mapping[str, tuple[float, int]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.removed or self.facts or self.edges)


@dataclass(frozen=True)
class _Nodes:
    ids: tuple[str, ...]
    index: dict[str, int]
    keep: np.ndarray | None  # old-node mask, None when nothing was removed
    remap: np.ndarray | None  # old index -> new index (-1 = removed)
    appended: tuple[str, ...]


def _node_space(snap: AdjacencySnapshot, delta: GraphDelta) -> _Nodes:
    present = snap.node_index
    gone = [f for f in delta.removed if f in present]
    appended = tuple(sorted(f for f in delta.facts if f not in present))
    if not gone:
        index = dict(present)
        base = len(snap.node_ids)
        index.update((fid, base + k) for k, fid in enumerate(appended))
        return _Nodes(snap.node_ids + appended, index, None, None, appended)
    keep = np.ones(len(snap.node_ids), dtype=bool)
    keep[[present[f] for f in gone]] = False
    remap = np.cumsum(keep, dtype=np.int64) - 1
    remap[~keep] = -1
    kept = tuple(fid for fid, k in zip(snap.node_ids, keep.tolist()) if k)
    ids = kept + appended
    return _Nodes(ids, {fid: i for i, fid in enumerate(ids)}, keep, remap, appended)


def _edge_arrays(snap: AdjacencySnapshot, nodes: _Nodes,
                 edges: Sequence[tuple[str, str, float]]):
    """CSR (indptr, indices, weights) over the new node space."""
    n = len(nodes.ids)
    add_src, add_dst, add_w = [], [], []
    for src, dst, weight in edges:
        i, j = nodes.index.get(src), nodes.index.get(dst)
        if i is None or j is None:
            continue
        add_src += (i, j)
        add_dst += (j, i)
        add_w += (float(weight), float(weight))
    if nodes.keep is None:
        pad = np.full(n - (snap.indptr.size - 1), snap.indptr[-1], dtype=np.int64)
        base = np.concatenate((snap.indptr, pad))
        if not add_src:
            return base, snap.indices, snap.weights
        return _insert_rows(snap, base, add_src, add_dst, add_w)
    rows = np.repeat(np.arange(snap.indptr.size - 1, dtype=np.int64), np.diff(snap.indptr))
    cols, weights = snap.indices, snap.weights
    if nodes.keep is not None:
        alive = nodes.keep[rows] & nodes.keep[cols]
        rows, cols, weights = nodes.remap[rows[alive]], nodes.remap[cols[alive]], weights[alive]
    if add_src:
        rows = np.concatenate((rows, np.asarray(add_src, dtype=np.int64)))
        cols = np.concatenate((cols, np.asarray(add_dst, dtype=np.int64)))
        weights = np.concatenate((weights, np.asarray(add_w, dtype=np.float64)))
        order = np.argsort(rows, kind="stable")  # tie-break: CSR row, then input edge ordinal
        rows, cols, weights = rows[order], cols[order], weights[order]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(rows, minlength=n), out=indptr[1:])
    return indptr, np.ascontiguousarray(cols), np.ascontiguousarray(weights)


def _insert_rows(snap: AdjacencySnapshot, base: np.ndarray, add_src: list[int],
                 add_dst: list[int], add_w: list[float]):
    """Additions only: insert each new entry at the end of its row, one pass."""
    rows = np.asarray(add_src, dtype=np.int64)
    order = np.argsort(rows, kind="stable")  # tie-break: CSR row, then input edge ordinal
    rows = rows[order]
    cols = np.asarray(add_dst, dtype=np.int64)[order]
    weights = np.asarray(add_w, dtype=np.float64)[order]
    at = base[rows + 1]
    indices = np.insert(snap.indices, at, cols)
    new_weights = np.insert(snap.weights, at, weights)
    grown = np.concatenate(([0], np.cumsum(np.bincount(rows, minlength=base.size - 1))))
    return base + grown, indices, new_weights


def _per_node(snap: AdjacencySnapshot, nodes: _Nodes, delta: GraphDelta):
    """PageRank and community over the new node space."""
    pagerank, community = snap.pagerank, snap.community
    if nodes.keep is not None:
        pagerank, community = pagerank[nodes.keep], community[nodes.keep]
    if nodes.appended:
        extra = [delta.metrics.get(fid, (0.0, -1)) for fid in nodes.appended]
        pagerank = np.concatenate((pagerank, np.asarray([m[0] for m in extra], np.float64)))
        community = np.concatenate((community, np.asarray([m[1] for m in extra], np.int64)))
    return pagerank, community


def _remapped_entities(snap: AdjacencySnapshot, nodes: _Nodes):
    """entity -> node indices with removed nodes dropped (order is kept)."""
    if nodes.keep is None:
        return list(snap.fact_to_entities), snap.entity_to_facts
    f2e = [ents for ents, k in zip(snap.fact_to_entities, nodes.keep.tolist()) if k]
    remap = nodes.remap.tolist()
    e2f: dict[str, tuple[int, ...]] = {}
    for eid, idxs in snap.entity_to_facts.items():
        moved = tuple(remap[i] for i in idxs if remap[i] >= 0)  # remap is monotone
        if moved:
            e2f[eid] = moved
    return f2e, e2f


def _entity_maps(snap: AdjacencySnapshot, nodes: _Nodes, delta: GraphDelta):
    """entity -> node indices, and node -> entity ids, over the new node space.

    Only the entities a changed fact names (before or after) are rebuilt; the
    rest of the map is shared with the old snapshot.
    """
    f2e, base = _remapped_entities(snap, nodes)
    f2e.extend(() for _ in nodes.appended)
    touched: dict[str, set[int]] = {}

    def members(eid: str) -> set[int]:
        found = touched.get(eid)
        if found is None:
            found = touched[eid] = set(base.get(eid, ()))
        return found

    for fid, ents in delta.facts.items():
        i = nodes.index[fid]
        new = tuple(dict.fromkeys(e for e in ents if isinstance(e, str) and e))
        for eid in set(f2e[i]).difference(new):
            members(eid).discard(i)
        for eid in new:
            members(eid).add(i)
        f2e[i] = new
    e2f = dict(base)
    for eid, idxs in touched.items():
        if idxs:
            e2f[eid] = tuple(sorted(idxs))
        else:
            e2f.pop(eid, None)
    return e2f, tuple(f2e)


def splice(snap: AdjacencySnapshot, delta: GraphDelta) -> AdjacencySnapshot:
    """A new snapshot equal, for the walk, to a fresh build after ``delta``."""
    if delta.empty:
        return snap
    nodes = _node_space(snap, delta)
    indptr, indices, weights = _edge_arrays(snap, nodes, delta.edges)
    pagerank, community = _per_node(snap, nodes, delta)
    e2f, f2e = _entity_maps(snap, nodes, delta)
    return AdjacencySnapshot(
        node_ids=nodes.ids, node_index=nodes.index,
        indptr=indptr, indices=indices, weights=weights,
        entity_to_facts=e2f, fact_to_entities=f2e,
        pagerank=pagerank, community=community, has_metrics=snap.has_metrics,
        source=snap.source, edge_count=int(indices.size // 2),
        fact_count=len(nodes.ids), profile_id=snap.profile_id,
        extras=dict(snap.extras),
    )


__all__ = ["GraphDelta", "splice"]
