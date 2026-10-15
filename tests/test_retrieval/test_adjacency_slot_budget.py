"""Measure the retained CSR payload without reading an operator's store."""

from types import SimpleNamespace

from superlocalmemory.retrieval.adjacency_slot import AdjSlot
from superlocalmemory.retrieval.graph_adjacency import snapshot_from_maps
from superlocalmemory.retrieval.graph_catchup import stamp


def test_slot_drops_builder_maps_and_measures_arrays():
    nodes = [f"fact-{i:04}" for i in range(256)]
    adj = {fid: [(nodes[(i + k) % len(nodes)], 0.5) for k in range(1, 25)]
           for i, fid in enumerate(nodes)}
    e2f = {"entity": nodes}
    f2e = {fid: ["entity"] for fid in nodes}
    snap = snapshot_from_maps(adj, e2f, f2e, {}, source="sqlite", profile_id="default")
    slot = AdjSlot(adj, e2f, f2e, set(nodes), snap.edge_count, len(nodes),
                   0.0, {}, "default", "sqlite", snap)
    retained = stamp(SimpleNamespace(), slot, ("default", False, False), None, now=0.0)

    assert retained.adj == retained.fact_to_entities == retained.graph_metrics == {}
    assert retained.entity_to_facts is snap.entity_to_facts
    assert retained.snapshot is snap
    arrays = (snap.indptr, snap.indices, snap.weights, snap.pagerank, snap.community)
    measured = sum(array.nbytes for array in arrays)
    # Two directed entries per undirected edge: int64 index + float64 weight.
    assert measured == 32 * snap.edge_count + 24 * snap.node_count + 8
    # The audit-sized graph's ARRAY payload. Python maps add workload-dependent
    # storage; this is neither total RSS nor a claim about the owner's database.
    assert 20_000_000 < 32 * 611_000 + 24 * 25_600 + 8 < 21_000_000
