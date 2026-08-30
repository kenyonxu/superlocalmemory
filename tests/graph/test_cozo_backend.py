# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Tests for CozoDBGraphBackend — Sprint 2."""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from superlocalmemory.graph.cozo_backend import (
    _COZO_AVAILABLE,
    CozoDBGraphBackend,
)

# CozoDB is an optional backend (pip install superlocalmemory[cozo]). When it
# is absent these tests cannot run — skip cleanly instead of erroring at
# fixture setup, so the suite stays honestly green on installs without it.
pytestmark = pytest.mark.skipif(
    not _COZO_AVAILABLE,
    reason="CozoDB optional dependency not installed (pip install superlocalmemory[cozo])",
)


@pytest.fixture
def backend():
    """Create temporary CozoDB backend."""
    path = "/tmp/test_slm_cozo_backend"
    be = CozoDBGraphBackend(path)
    yield be
    be.close()
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def populated(backend):
    """Backend with test graph: e1→e2→e3, e1→e3, e4→e1."""
    backend.add_entity("e1", "Node1", "concept")
    backend.add_entity("e2", "Node2", "concept")
    backend.add_entity("e3", "Node3", "concept")
    backend.add_entity("e4", "Node4", "isolated")
    backend.add_edge("e1", "e2", "links", 1.0)
    backend.add_edge("e2", "e3", "links", 1.0)
    backend.add_edge("e1", "e3", "direct", 0.5)
    return backend


class TestLifecycle:
    """Open, close, health check."""

    def test_open_and_close(self):
        be = CozoDBGraphBackend("/tmp/test_cozo_lifecycle")
        health = be.health_check()
        assert health["status"] == "active"
        be.close()
        shutil.rmtree("/tmp/test_cozo_lifecycle", ignore_errors=True)

    def test_entities_persist(self, populated):
        health = populated.health_check()
        assert health["entities"] == 4
        assert health["edges"] == 3


class TestItSuppliesDataNotAnswers:
    """The backend is a store. The traversal is not its job any more.

    ``spreading_activation`` and ``recall_facts`` used to live here and were a
    second implementation of the walk in ``retrieval/entity_channel.py``. They
    computed a different function — no PageRank factor — so on a real store 3,567
    of 3,667 shared facts scored differently, the result sets diverged, and the
    projected path failed its shadow comparison on every query. The walk is now
    one pure function over an ``AdjacencySnapshot``
    (``retrieval/spreading.py``), and these tests pin the boundary that keeps it
    that way.
    """

    def test_the_backend_exposes_no_traversal(self, populated):
        """A traversal here is a second walk, and a second walk drifts."""
        for banned in ("spreading_activation", "recall_facts"):
            assert not hasattr(populated, banned), (
                f"{banned} is back on the graph backend. Add an AdjacencySource "
                "adapter in retrieval/graph_adjacency.py instead — the walk is "
                "one function and it does not belong to a storage engine."
            )

    def test_it_hands_over_its_edges_in_one_query(self, populated):
        """What an adjacency source needs: every edge, in a single batch.

        The retired traversal asked Cozo one question per frontier node, which
        measured 18,125 ms for 200 nodes against 303 ms batched for the same
        6,046 rows. A source reads the relation whole.
        """
        rows = populated._db.run(
            "?[from_id, to_id, weight] := "
            "*edge{from_id, to_id, edge_type, weight, profile_id}"
        ).values.tolist()
        assert len(rows) == populated.health_check()["edges"]
        assert all(len(row) == 3 for row in rows)

    def test_it_hands_over_its_entity_bridge(self, populated):
        """The bridge is how a query's entities enter the fact graph."""
        populated.add_fact_entities("fact-1", ["e1"], "default")
        rows = populated._db.run(
            "?[fact_id, entity_id] := *fact_entity{fact_id, entity_id, profile_id}"
        ).values.tolist()
        assert ["fact-1", "e1"] in rows


class TestCanonicalEntityProjection:
    def test_bulk_projection_keeps_canonical_and_fact_namespaces_distinct(self, backend):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE canonical_entities (
                entity_id TEXT, canonical_name TEXT, entity_type TEXT,
                first_seen TEXT, last_seen TEXT, fact_count INTEGER, profile_id TEXT
            );
            CREATE TABLE atomic_facts (
                fact_id TEXT, canonical_entities_json TEXT, profile_id TEXT
            );
            CREATE TABLE graph_edges (
                source_id TEXT, target_id TEXT, edge_type TEXT, weight REAL, profile_id TEXT
            );
            INSERT INTO canonical_entities VALUES
                ('entity-ada', 'Ada', 'person', '2026-01-01', '2026-01-02', 2, 'default'),
                ('entity-atlas', 'Atlas', 'project', '2026-01-01', '2026-01-02', 1, 'default');
            INSERT INTO atomic_facts VALUES
                ('fact-1', '["entity-ada"]', 'default'),
                ('fact-2', '["entity-ada", "entity-atlas"]', 'default');
            INSERT INTO graph_edges VALUES ('fact-1', 'fact-2', 'related', 1.0, 'default');
        """)
        backend.bulk_import_from_sqlite(conn)
        health = backend.health_check()
        assert health["entities"] == 2
        assert health["edges"] == 1
        # Assert the bridge, not a traversal over it: what this projection owes
        # a caller is the mapping, and the walk that consumes it lives elsewhere.
        bridge = backend._db.run(
            "?[fact_id] := *fact_entity{fact_id, entity_id, profile_id}, "
            "entity_id = $e", {"e": "entity-ada"},
        ).values.tolist()
        assert sorted(row[0] for row in bridge) == ["fact-1", "fact-2"]

    def test_bulk_projection_preserves_parallel_typed_fact_edges(self, backend):
        """Projection parity must not collapse distinct canonical edge types."""
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE canonical_entities (
                entity_id TEXT, canonical_name TEXT, entity_type TEXT,
                first_seen TEXT, last_seen TEXT, fact_count INTEGER, profile_id TEXT
            );
            CREATE TABLE atomic_facts (
                fact_id TEXT, canonical_entities_json TEXT, profile_id TEXT
            );
            CREATE TABLE graph_edges (
                source_id TEXT, target_id TEXT, edge_type TEXT, weight REAL, profile_id TEXT
            );
            INSERT INTO graph_edges VALUES
                ('fact-1', 'fact-2', 'related', 1.0, 'default'),
                ('fact-1', 'fact-2', 'temporal', 0.8, 'default');
        """)
        backend.bulk_import_from_sqlite(conn)
        assert backend.health_check()["edges"] == 2

    def test_bulk_projection_normalizes_duplicate_logical_edges_to_max_weight(self, backend):
        """Legacy duplicate rows project once with their strongest weight."""
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE canonical_entities (
                entity_id TEXT, canonical_name TEXT, entity_type TEXT,
                first_seen TEXT, last_seen TEXT, fact_count INTEGER, profile_id TEXT
            );
            CREATE TABLE atomic_facts (
                fact_id TEXT, canonical_entities_json TEXT, profile_id TEXT
            );
            CREATE TABLE graph_edges (
                source_id TEXT, target_id TEXT, edge_type TEXT, weight REAL, profile_id TEXT
            );
            INSERT INTO graph_edges VALUES
                ('fact-1', 'fact-2', 'supersedes', 0.4, 'default'),
                ('fact-1', 'fact-2', 'supersedes', 1.0, 'default'),
                ('fact-1', 'fact-2', 'temporal', 0.8, 'default');
        """)

        imported = backend.bulk_import_from_sqlite(conn)
        rows = backend._db.run(
            "?[edge_type, weight] := "
            "*edge{from_id, to_id, edge_type, weight, profile_id}, "
            "from_id = $source, to_id = $target, profile_id = $profile",
            {"source": "fact-1", "target": "fact-2", "profile": "default"},
        ).values.tolist()

        assert imported == 2
        assert backend.health_check()["edges"] == 2
        assert sorted(rows) == [["supersedes", 1.0], ["temporal", 0.8]]

    def test_remove_fact_is_parameter_safe_and_removes_derived_records(self, backend):
        unsafe_id = "fact-'quoted'"
        backend.add_entity("entity-ada", "Ada", "person")
        backend.add_fact_entities(unsafe_id, ["entity-ada"])
        backend.add_edge(unsafe_id, "fact-2", "related")
        backend.remove_fact(unsafe_id)
        bridge = backend._db.run(
            "?[fact_id] := *fact_entity{fact_id, entity_id, profile_id}, "
            "entity_id = $e", {"e": "entity-ada"},
        ).values.tolist()
        assert bridge == []
        assert backend.health_check()["edges"] == 0

    def test_withdrawing_a_fact_keeps_the_graph_shape(self, backend):
        """Candidacy and adjacency are different things.

        A withheld fact must stop being offered as a result while the edges its
        visible neighbours are reached through stay put — the retrieval channel
        prunes its entity map on visibility and its edge walk on scope alone, so
        a projection that deleted the edges would answer differently from it.
        """
        backend.add_entity("entity-ada", "Ada", "person")
        backend.add_fact_entities("fact-1", ["entity-ada"])
        backend.add_edge("fact-1", "fact-2", "related")

        backend.remove_fact_candidacy("fact-1")

        bridge = backend._db.run(
            "?[fact_id] := *fact_entity{fact_id, entity_id, profile_id}, "
            "entity_id = $e", {"e": "entity-ada"},
        ).values.tolist()
        assert bridge == [], "the fact is still being offered as a candidate"
        assert backend.health_check()["edges"] == 1, (
            "withdrawing a fact deleted an edge its neighbour needs"
        )


class TestPageRank:
    """Iterative PageRank."""

    def test_pagerank_returns_all_entities(self, populated):
        pr = populated.pagerank()
        assert len(pr) == 4
        assert all(v > 0 for v in pr.values())

    def test_empty_backend(self, backend):
        pr = backend.pagerank()
        assert pr == {}


class TestCommunityDetection:
    """Connected components."""

    def test_connected_component(self, populated):
        communities = populated.community_detect()
        assert len(set(communities.values())) == 2  # {e1,e2,e3} + {e4}

    def test_same_community(self, populated):
        communities = populated.community_detect()
        assert communities["e1"] == communities["e2"] == communities["e3"]
        assert communities["e4"] != communities["e1"]


class TestShortestPath:
    """BFS shortest path."""

    def test_same_node(self, populated):
        assert populated.shortest_path("e1", "e1") == ["e1"]

    def test_direct_edge(self, populated):
        path = populated.shortest_path("e1", "e2")
        assert path == ["e1", "e2"]

    def test_two_hop(self, populated):
        path = populated.shortest_path("e1", "e3")
        assert path in (["e1", "e3"], ["e1", "e2", "e3"])

    def test_no_path(self, populated):
        assert populated.shortest_path("e1", "e4") == []


class TestTierSync:
    """sync_tier_changes modifies entity tiers."""

    def test_tier_sync_no_crash(self, populated):
        """sync_tier_changes should not raise."""
        populated.sync_tier_changes(added=["e1"], removed=["e4"])
