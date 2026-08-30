# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The worker that carries queued facts into the graph and the vector store.

The projection must hold exactly the set of memories recall is allowed to
return — no more, no less. More means a forgotten or withheld memory stays
reachable through the graph. Less means a stored memory cannot be recalled. The
inline sync this worker replaced got both wrong: it projected only
``lifecycle in (active, warm)``, so live ``cold`` facts were missing, and it
never removed a fact that had since been archived.

These tests drive the worker against fake backends that record what they were
asked to do, because the assertion that matters is *which* facts reached the
projection and which were taken out of it.
"""

from __future__ import annotations

import threading

import pytest

from superlocalmemory.core.projection_drain import ProjectionDrain
from superlocalmemory.storage import projection_outbox
from superlocalmemory.storage.models import (
    AtomicFact,
    EdgeType,
    GraphEdge,
    MemoryLifecycle,
    MemoryRecord,
)

_PARENT_MEMORY = "m-drain"


class FakeGraph:
    """Records every projection call. Optionally refuses to accept a fact."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.removed: list[str] = []
        self.withdrawn: list[str] = []
        self.facts: dict[str, list[str]] = {}
        self.entities: dict[str, str] = {}
        self.edges: list[tuple[str, str, str, float]] = []
        self._fail_on = fail_on or set()

    def remove_fact(self, fact_id: str) -> None:
        """Full removal, edges included — mirrors CozoDBGraphBackend."""
        self.removed.append(fact_id)
        self.facts.pop(fact_id, None)
        self.edges = [e for e in self.edges if fact_id not in (e[0], e[1])]

    def remove_fact_candidacy(self, fact_id: str) -> None:
        """Bridge only. Edges survive, exactly as the real backend leaves them."""
        self.withdrawn.append(fact_id)
        self.facts.pop(fact_id, None)

    def add_entity(self, entity_id, name, entity_type, meta, profile_id) -> None:
        self.entities[entity_id] = name

    def add_fact_entities(self, fact_id, entities, profile_id) -> None:
        if fact_id in self._fail_on:
            raise RuntimeError("cozo refused this fact")
        self.facts[fact_id] = list(entities)

    def add_edge(self, source, target, edge_type, weight, profile_id="default") -> None:
        self.edges.append((source, target, edge_type, weight))


class FakeVector:
    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.removed: list[str] = []

    def add_vectors(self, ids, embeddings, tiers, profile_id) -> None:
        for fid, emb in zip(ids, embeddings):
            self.vectors[fid] = list(emb)

    def remove_vector(self, fact_id: str) -> None:
        self.removed.append(fact_id)
        self.vectors.pop(fact_id, None)


@pytest.fixture
def store(engine_with_mock_deps):
    manager = engine_with_mock_deps._db
    manager.store_memory(MemoryRecord(
        memory_id=_PARENT_MEMORY, profile_id="default", content="parent",
    ))
    manager.execute("DELETE FROM projection_outbox")
    return manager


_DEFAULT_EMBEDDING = object()


def _store_fact(db, content: str, *, lifecycle=MemoryLifecycle.ACTIVE,
                embedding=_DEFAULT_EMBEDDING) -> AtomicFact:
    """Store a fact. ``embedding=None`` means genuinely none, not "use the default"."""
    fact = AtomicFact(
        memory_id=_PARENT_MEMORY, profile_id="default", content=content,
        lifecycle=lifecycle,
        embedding=[0.1] * 8 if embedding is _DEFAULT_EMBEDDING else embedding,
    )
    db.store_fact(fact)
    return fact


def _drain_for(db, graph=None, vector=None) -> ProjectionDrain:
    return ProjectionDrain(db, lambda: graph, lambda: vector)


# ---------------------------------------------------------------------------
# The set the projection is supposed to hold
# ---------------------------------------------------------------------------

class TestWhatGetsProjected:

    def test_a_stored_fact_reaches_both_projections(self, store) -> None:
        fact = _store_fact(store, "a fact worth projecting")
        graph, vector = FakeGraph(), FakeVector()

        result = _drain_for(store, graph, vector).drain_once()

        assert result.projected == 1, result.errors
        assert fact.fact_id in graph.facts
        assert fact.fact_id in vector.vectors
        assert projection_outbox.depth(store) == 0

    def test_a_cold_fact_is_projected_too(self, store) -> None:
        """``cold`` is a live tier; recall answers from it.

        The inline sync this replaced filtered on
        ``lifecycle in ("active", "warm")``, so on any store old enough to have
        aged facts — which is every real store — the bulk of the graph was
        simply not projected.
        """
        fact = _store_fact(store, "aged but still mine", lifecycle=MemoryLifecycle.COLD)
        graph = FakeGraph()

        _drain_for(store, graph, FakeVector()).drain_once()

        assert fact.fact_id in graph.facts, (
            "a cold fact was skipped, so recall would miss a memory the store has"
        )

    def test_a_facts_edges_go_with_it(self, store) -> None:
        left = _store_fact(store, "left")
        right = _store_fact(store, "right")
        store.store_edge(GraphEdge(
            profile_id="default", source_id=left.fact_id,
            target_id=right.fact_id, edge_type=EdgeType.SEMANTIC, weight=0.7,
        ))
        graph = FakeGraph()

        _drain_for(store, graph, FakeVector()).drain_once()

        assert any(
            {e[0], e[1]} == {left.fact_id, right.fact_id} for e in graph.edges
        ), "the fact was projected without the edge that makes it reachable"

    def test_a_fact_with_no_embedding_yet_is_not_an_error(self, store) -> None:
        """Ingestion is queryable-first, so the vector arrives later.

        ``None`` is the real un-enriched state. An empty *list* is not: the
        codec encodes it as a zero-length blob, which it then refuses to
        decode — deliberately, so an absent vector can never be confused with
        a lost one. The live store has no such row, and a fact that had one
        would fail to project and say so, which is correct.
        """
        fact = _store_fact(store, "no vector yet", embedding=None)
        vector = FakeVector()

        result = _drain_for(store, FakeGraph(), vector).drain_once()

        assert result.failed == 0
        assert result.projected == 1
        assert fact.fact_id not in vector.vectors


# ---------------------------------------------------------------------------
# The set it is supposed to NOT hold
# ---------------------------------------------------------------------------

class TestWhatGetsTakenOut:

    def test_a_withheld_fact_leaves_the_projection_entirely(self, store) -> None:
        """A withheld memory loses its bridge AND its edges.

        Corrected 2026-08-22 after measuring what the SQLite channel actually
        does. Its raw edge query filters on scope only, which reads like
        "adjacency ignores visibility" — but it then prunes the result:
        "Edge scope alone cannot authorize an endpoint. Prune both endpoints
        against the visible fact corpus so denied facts cannot influence an
        allowed candidate indirectly through propagation."

        So the projected graph must not contain a withheld fact at all. An
        earlier version of this file asserted the opposite, on the strength of
        the raw query alone. Measured cost of getting it wrong: Cozo's bridge
        held 1,257 unreturnable facts and its edges touched 805, and the Cozo
        graph search diverged from SQLite on every query tried — one returned 9
        results against SQLite's 20, because withheld facts had taken the top-k
        budget on their pooled entity lists.
        """
        visible = _store_fact(store, "a memory that stays visible")
        withheld = _store_fact(store, "a memory about to be withheld")
        store.store_edge(GraphEdge(
            profile_id="default", source_id=visible.fact_id,
            target_id=withheld.fact_id, edge_type=EdgeType.SEMANTIC, weight=0.9,
        ))
        graph, vector = FakeGraph(), FakeVector()
        drain = _drain_for(store, graph, vector)
        drain.drain_once()
        assert graph.edges, "nothing was projected, so this proves nothing"

        store.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?",
            (withheld.fact_id,),
        )
        projection_outbox.enqueue(store, withheld.fact_id, "default")
        drain.drain_once()

        assert withheld.fact_id not in graph.facts, "it is still being offered"
        assert withheld.fact_id in vector.removed
        assert not any(
            withheld.fact_id in (e[0], e[1]) for e in graph.edges
        ), (
            "a withheld fact is still reachable through an edge, so the "
            "projected walk sees an adjacency the SQLite walk prunes away"
        )

    def test_a_deleted_fact_loses_its_edges_too(self, store) -> None:
        """Gone is different from hidden: a hard delete takes the edges out of
        SQLite as well, so the projection must not keep an adjacency the store
        no longer has."""
        left = _store_fact(store, "survives")
        gone = _store_fact(store, "about to be deleted outright")
        store.store_edge(GraphEdge(
            profile_id="default", source_id=left.fact_id,
            target_id=gone.fact_id, edge_type=EdgeType.SEMANTIC, weight=0.9,
        ))
        graph, vector = FakeGraph(), FakeVector()
        drain = _drain_for(store, graph, vector)
        drain.drain_once()

        store.delete_fact(gone.fact_id)
        drain.drain_once()

        assert gone.fact_id in graph.removed
        assert not any(gone.fact_id in (e[0], e[1]) for e in graph.edges)

    def test_a_queued_delete_removes_from_both(self, store) -> None:
        fact = _store_fact(store, "about to be forgotten")
        graph, vector = FakeGraph(), FakeVector()
        _drain_for(store, graph, vector).drain_once()
        assert fact.fact_id in graph.facts

        store.delete_fact(fact.fact_id)
        result = _drain_for(store, graph, vector).drain_once()

        assert result.removed == 1
        assert fact.fact_id not in graph.facts
        assert fact.fact_id not in vector.vectors

    def test_an_id_that_is_not_a_fact_is_skipped(self, store) -> None:
        """Edge endpoints may be entity ids; those are not facts to project."""
        projection_outbox.enqueue(store, "entity-not-a-fact", "default")

        result = _drain_for(store, FakeGraph(), FakeVector()).drain_once()

        assert result.skipped == 1
        assert result.failed == 0
        assert projection_outbox.depth(store) == 0


# ---------------------------------------------------------------------------
# Failure, and what it must not do
# ---------------------------------------------------------------------------

class TestFailureIsLoudAndRecoverable:

    def test_a_refused_fact_stays_queued_with_the_error(self, store) -> None:
        fact = _store_fact(store, "cozo will refuse this one")
        graph = FakeGraph(fail_on={fact.fact_id})

        result = _drain_for(store, graph, FakeVector()).drain_once()

        assert result.failed == 1
        assert projection_outbox.depth(store) == 1
        row = dict(store.execute(
            "SELECT * FROM projection_outbox WHERE fact_id = ?", (fact.fact_id,),
        )[0])
        assert row["attempts"] == 1
        assert "refused" in (row["last_error"] or "")

    def test_a_refused_fact_projects_once_the_backend_recovers(self, store) -> None:
        """Nothing is dropped, so a transient failure costs a retry and no data."""
        fact = _store_fact(store, "transiently refused")
        broken = FakeGraph(fail_on={fact.fact_id})
        _drain_for(store, broken, FakeVector()).drain_once()

        healthy = FakeGraph()
        result = _drain_for(store, healthy, FakeVector()).drain_once()

        assert result.projected == 1
        assert fact.fact_id in healthy.facts
        assert projection_outbox.depth(store) == 0

    def test_a_poisoned_row_does_not_starve_the_queue(self, store) -> None:
        """One impossible fact must not stop every fact behind it.

        The queue is claimed in ``attempts, revision`` order, so a row that has
        already failed sorts behind fresh work instead of being retried first
        forever at the head of the queue.

        ``limit=1`` is the whole test. With a batch bigger than the queue every
        row is claimed on every pass and the ordering never matters, so the
        first version of this test passed against ``ORDER BY revision`` — which
        starves — and proved nothing. A batch of one is the only way to observe
        which row the drain reaches for.
        """
        poison = _store_fact(store, "permanently unprojectable")
        graph = FakeGraph(fail_on={poison.fact_id})
        drain = _drain_for(store, graph, FakeVector())
        drain.drain_once(limit=1)  # poison now has attempts=1
        assert projection_outbox.depth(store) == 1

        healthy = _store_fact(store, "queued behind the poison")
        drain.drain_once(limit=1)

        assert healthy.fact_id in graph.facts, (
            "the drain reached for the failed row again and the healthy fact "
            "behind it was never projected"
        )

    def test_no_projection_means_nothing_is_touched(self, store) -> None:
        """A store with no backend keeps its queue; the rows are pending work."""
        _store_fact(store, "queued for a projection that does not exist yet")
        before = projection_outbox.depth(store)

        result = _drain_for(store, None, None).drain_once()

        assert result.as_dict() == {
            "projected": 0, "removed": 0, "failed": 0,
            "skipped": 0, "superseded": 0,
        }
        assert projection_outbox.depth(store) == before


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class TestReplayIsSafe:

    def test_projecting_twice_leaves_one_copy(self, store) -> None:
        """The row carries an id, not a snapshot, so a replay writes the present.

        ``remove_fact`` runs before every re-projection, which is what makes a
        redelivery after a crash produce the same graph rather than a doubled
        one.
        """
        fact = _store_fact(store, "projected twice")
        graph, vector = FakeGraph(), FakeVector()
        drain = _drain_for(store, graph, vector)
        drain.drain_once()

        projection_outbox.enqueue(store, fact.fact_id, "default")
        drain.drain_once()

        assert list(graph.facts) == [fact.fact_id]
        assert len(vector.vectors) == 1

    def test_a_stale_edge_does_not_survive_a_replay(self, store) -> None:
        """Re-projection reflects SQLite now, not SQLite when it was queued."""
        left = _store_fact(store, "kept")
        right = _store_fact(store, "unlinked later")
        store.store_edge(GraphEdge(
            profile_id="default", source_id=left.fact_id,
            target_id=right.fact_id, edge_type=EdgeType.SEMANTIC, weight=0.7,
        ))
        graph = FakeGraph()
        drain = _drain_for(store, graph, FakeVector())
        drain.drain_once()
        assert graph.edges

        store.execute("DELETE FROM graph_edges WHERE source_id = ?", (left.fact_id,))
        projection_outbox.enqueue(store, left.fact_id, "default")
        drain.drain_once()

        assert not any(e[0] == left.fact_id for e in graph.edges), (
            "a replay re-created an edge SQLite no longer has"
        )


# ---------------------------------------------------------------------------
# The worker thread
# ---------------------------------------------------------------------------

class TestTheWorker:

    def test_a_notified_worker_projects_without_being_polled(self, store) -> None:
        """The gap between remembered and recallable is milliseconds, not seconds."""
        graph, vector = FakeGraph(), FakeVector()
        drain = _drain_for(store, graph, vector)
        assert drain.start()
        try:
            fact = _store_fact(store, "stored while the worker is running")
            drain.notify()
            deadline = threading.Event()
            for _ in range(100):
                if fact.fact_id in graph.facts:
                    break
                deadline.wait(0.02)
            assert fact.fact_id in graph.facts, (
                "the worker was notified and did not project within 2 s"
            )
        finally:
            drain.stop()
        assert not drain.running

    def test_starting_twice_does_not_make_two_workers(self, store) -> None:
        drain = _drain_for(store, FakeGraph(), FakeVector())
        assert drain.start() is True
        try:
            assert drain.start() is False
        finally:
            drain.stop()
