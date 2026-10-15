# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The graph and Lance projections lose the profile's share, history included.

Per-fact removal left the graph's entity nodes (each carries a name) and left every
earlier version of a Lance table on disk, where a deleted vector stays readable.
"""

from __future__ import annotations

import importlib.util
import struct

import pytest

pytestmark = pytest.mark.native


@pytest.mark.skipif(importlib.util.find_spec("pycozo") is None, reason="CozoDB not installed")
def test_the_profiles_entity_nodes_leave_the_graph(tmp_path) -> None:
    from superlocalmemory.graph.cozo_backend import CozoDBGraphBackend

    graph = CozoDBGraphBackend(str(tmp_path / "cozo"))
    try:
        graph.add_entity("e-alice", "Alice Zebrowski", "person", profile_id="gone")
        graph.add_entity("e-bob", "Bob Quill", "person", profile_id="kept")
        graph.add_fact_entities("f1", ["e-alice"], profile_id="gone")
        graph.add_fact_entities("f2", ["e-bob"], profile_id="kept")
        graph.add_edge("e-alice", "e-alice", "knows", profile_id="gone")

        graph.remove_profile("gone")

        names = [r[0] for r in graph._db.run("?[name] := *entity{name}").values.tolist()]
        assert names == ["Bob Quill"]
        bridges = graph._db.run("?[fact_id] := *fact_entity{fact_id}").values.tolist()
        assert [r[0] for r in bridges] == ["f2"]
        assert graph._db.run("?[a] := *edge{from_id: a}").values.tolist() == []
    finally:
        graph.close()


@pytest.mark.skipif(importlib.util.find_spec("lancedb") is None, reason="LanceDB not installed")
def test_a_deleted_vector_is_not_left_in_an_earlier_table_version(tmp_path) -> None:
    from datetime import timedelta

    from superlocalmemory.vector.lancedb_backend import LanceDBVectorBackend

    backend = LanceDBVectorBackend(str(tmp_path / "lance"), dimension=8)
    erased_vector = struct.pack("<8f", *([0.3125] * 8))  # a pattern nothing else contains
    try:
        backend.add_vectors(["fact-to-erase-9001"], [[0.3125] * 8], ["active"], profile_id="gone")
        backend.add_vectors(["fact-kept-42"], [[0.25] * 8], ["active"], profile_id="kept")
        backend.remove_vector("fact-to-erase-9001")
        assert erased_vector in _lance_bytes(tmp_path), \
            "the table history still holds the deleted vector: the premise of this test"

        result = backend.compact(retention=timedelta(0))

        assert result["ok"] is True
        assert erased_vector not in _lance_bytes(tmp_path)
        assert backend.fact_ids() == ["fact-kept-42"]
    finally:
        backend.close()


def _lance_bytes(root) -> bytes:
    return b"".join(p.read_bytes() for p in (root / "lance").rglob("*") if p.is_file())


def test_the_profile_sweep_calls_both_and_counts_a_refusal() -> None:
    from datetime import timedelta

    from superlocalmemory.compliance.gdpr import GDPRCompliance

    seen: list[object] = []

    class Graph:
        def remove_profile(self, profile_id):
            seen.append(("graph", profile_id))

    class Vector:
        def compact(self, *, retention):
            seen.append(("lance", retention))
            return {"ok": False, "reason": "busy"}

    failures = GDPRCompliance._sweep_projection_profile(Graph(), Vector(), "gone")

    assert seen == [("graph", "gone"), ("lance", timedelta(0))]
    assert failures == 1, "a history cleanup that did not happen is a failure"

    class Refusing:
        def remove_profile(self, profile_id):
            raise RuntimeError("rocksdb is locked")

    assert GDPRCompliance._sweep_projection_profile(Refusing(), None, "gone") == 1
