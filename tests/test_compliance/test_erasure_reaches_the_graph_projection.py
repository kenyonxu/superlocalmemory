# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Erasing a profile must remove its memories from the graph and vector stores.

CozoDB and LanceDB are separate storage engines. Deleting rows out of SQLite
does not reach either of them, so a profile wipe that only touches SQLite leaves
every one of that tenant's memories reachable through the graph — and the
erasure receipt says the erasure completed.

There is a second, sharper version of the same problem. The queued removals live
in ``projection_outbox``, which carries a ``profile_id`` column, and the wipe
finds a tenant's tables by looking for exactly that column. So the sweep deletes
the rows holding the queued removals: the intent to unproject is erased along
with the data, and nothing is left to take the facts out of the graph. The purge
therefore has to happen before the sweep, not be left to the drain.
"""

from __future__ import annotations

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.core import backend_orchestrator
from superlocalmemory.storage import projection_outbox
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


class _FakeGraph:
    def __init__(self, refuse: set[str] | None = None) -> None:
        self.facts: set[str] = set()
        self._refuse = refuse or set()

    def remove_fact(self, fact_id: str) -> None:
        if fact_id in self._refuse:
            raise RuntimeError("cozo refused the delete")
        self.facts.discard(fact_id)


class _FakeVector:
    def __init__(self) -> None:
        self.vectors: set[str] = set()

    def remove_vector(self, fact_id: str) -> None:
        self.vectors.discard(fact_id)


class _StubOrchestrator:
    def __init__(self, graph, vector) -> None:
        self._graph, self._vector = graph, vector

    def get_graph_backend(self):
        return self._graph

    def get_vector_backend(self):
        return self._vector


@pytest.fixture
def store(engine_with_mock_deps):
    """A store with two tenants, each with a parent memory row.

    ``atomic_facts`` has foreign keys on both ``profile_id`` and ``memory_id``,
    and these connections enforce them, so a tenant needs a profile row and a
    memory of its own before it can own a fact.
    """
    db = engine_with_mock_deps._db
    for profile_id in ("tenant-a", "tenant-b"):
        db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
            (profile_id, profile_id),
        )
        db.store_memory(MemoryRecord(
            memory_id=f"m-{profile_id}", profile_id=profile_id, content="parent",
        ))
    return db


def _seed(db, profile_id: str, count: int) -> list[str]:
    ids = []
    for i in range(count):
        fact = AtomicFact(
            memory_id=f"m-{profile_id}", profile_id=profile_id,
            content=f"{profile_id} memory {i}", embedding=[0.2] * 8,
        )
        db.store_fact(fact)
        ids.append(fact.fact_id)
    return ids


@pytest.fixture
def with_projection(request):
    """Install a stub orchestrator, and always put the real one back."""
    installed: list = []

    def _install(graph, vector):
        previous = backend_orchestrator.get_orchestrator()
        installed.append(previous)
        backend_orchestrator.set_orchestrator(_StubOrchestrator(graph, vector))

    yield _install
    for previous in reversed(installed):
        backend_orchestrator._orchestrator = previous


def test_a_tenants_facts_leave_the_graph(store, with_projection) -> None:
    mine = _seed(store, "tenant-a", 3)
    graph, vector = _FakeGraph(), _FakeVector()
    graph.facts.update(mine)
    vector.vectors.update(mine)
    with_projection(graph, vector)

    purged, failures = GDPRCompliance(
        db=store,
    )._purge_graph_and_vector_projections("tenant-a")

    assert purged == 3
    assert failures == 0
    assert graph.facts == set()
    assert vector.vectors == set()


def test_another_tenants_facts_are_left_alone(store, with_projection) -> None:
    mine = _seed(store, "tenant-a", 2)
    theirs = _seed(store, "tenant-b", 2)
    graph = _FakeGraph()
    graph.facts.update(mine + theirs)
    with_projection(graph, _FakeVector())

    GDPRCompliance(db=store)._purge_graph_and_vector_projections("tenant-a")

    assert graph.facts == set(theirs), (
        "the purge crossed a tenant boundary in the projection"
    )


def test_a_projection_that_refuses_is_counted_not_swallowed(
    store, with_projection,
) -> None:
    """An erasure that could not reach the graph must not report success."""
    mine = _seed(store, "tenant-a", 2)
    graph = _FakeGraph(refuse={mine[0]})
    graph.facts.update(mine)
    with_projection(graph, _FakeVector())

    purged, failures = GDPRCompliance(
        db=store,
    )._purge_graph_and_vector_projections("tenant-a")

    assert failures == 1
    assert purged == 1


def test_no_projection_is_not_a_failure(store, with_projection) -> None:
    """Most installs have no graph backend open; that is not incomplete erasure."""
    _seed(store, "tenant-a", 2)
    with_projection(None, None)

    purged, failures = GDPRCompliance(
        db=store,
    )._purge_graph_and_vector_projections("tenant-a")

    assert (purged, failures) == (0, 0)


def test_the_queued_removals_are_swept_with_the_tenant(store) -> None:
    """The outbox is tenant-scoped, so erasure takes its rows too.

    This is the reason the purge above cannot be left to the drain: by the time
    the drain looked, the rows telling it what to remove would be gone.
    """
    mine = _seed(store, "tenant-a", 2)
    theirs = _seed(store, "tenant-b", 1)

    queued = {
        dict(r)["fact_id"]: dict(r)["profile_id"]
        for r in store.execute("SELECT fact_id, profile_id FROM projection_outbox")
    }
    assert set(mine) <= set(queued), "the tenant's facts were never queued"
    assert all(queued[f] == "tenant-a" for f in mine), (
        f"queued under the wrong tenant: {queued} — erasure finds a tenant's "
        "tables by profile_id, so a wrong one survives that tenant's wipe"
    )
    assert all(queued[f] == "tenant-b" for f in theirs)


def test_a_delete_is_queued_under_the_facts_own_tenant(store) -> None:
    """``delete_fact`` is often called with no profile, and must still get it right.

    The tenant has to be read before the row goes. Filing the removal under
    ``default`` would leave it behind when the real tenant is erased.
    """
    fact_id = _seed(store, "tenant-a", 1)[0]
    store.execute("DELETE FROM projection_outbox")

    store.delete_fact(fact_id)

    row = dict(store.execute(
        "SELECT * FROM projection_outbox WHERE fact_id = ?", (fact_id,),
    )[0])
    assert row["op"] == projection_outbox.OP_DELETE
    assert row["profile_id"] == "tenant-a"
