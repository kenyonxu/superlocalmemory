# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The graph projection is a faster place to read the same edges, or it is a bug.

Until this reader existed the projection had none: written on every store,
drained through an outbox, held at bidirectional parity with SQLite, purged on
erasure -- and queried by nothing. That is why it was worth building and why it
was worth nothing.

What it buys, measured on a copy of the author's 208,151-edge store: reading one
profile's adjacency in 395 ms against SQLite's 2,477 ms, and that rebuild sits on
the recall path. What it must not buy is a different answer. On the same store,
all fifteen probe queries returned byte-identical ids and scores from both
sources, and an exhaustive comparison of all 4,034 nodes' adjacency sets found
the projection missing exactly 57 entries -- every one of them a weaker duplicate
of an edge it kept, so a walk that takes the maximum cannot observe them.

These tests pin the boundary in code: same answer, and an honest refusal for the
scopes the projection cannot answer.
"""

from __future__ import annotations

import pytest

from superlocalmemory.graph.cozo_adjacency import CozoAdjacencySource


class _Result:
    """What the projection client returns: something with ``.values.tolist()``."""

    def __init__(self, rows: list[list]) -> None:
        self.values = self
        self._rows = rows

    def tolist(self) -> list[list]:
        return self._rows

    def __len__(self) -> int:
        return len(self._rows)


class _Client:
    def __init__(self, rows: list[list] | None = None, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def run(self, script: str, params: dict | None = None):
        self.calls.append((script, params or {}))
        if self._raises:
            raise RuntimeError("projection is not open")
        return _Result(self._rows)


class _Backend:
    def __init__(self, client) -> None:
        self._db = client


class TestItSuppliesEdgesAndNothingElse:
    def test_it_returns_the_triples_the_walk_needs(self) -> None:
        client = _Client([["a", "b", 0.5], ["b", "c", 0.25]])
        source = CozoAdjacencySource(_Backend(client))

        assert source.edges("default") == [("a", "b", 0.5), ("b", "c", 0.25)]

    def test_it_asks_for_one_profile_only(self) -> None:
        """One relation holds every profile's edges. An unfiltered read would
        walk one profile's memories through another's graph."""
        client = _Client([])
        CozoAdjacencySource(_Backend(client)).edges("someone-else")

        script, params = client.calls[0]
        assert params == {"pid": "someone-else"}
        assert "profile_id: $pid" in script

    def test_it_exposes_no_way_to_ask_for_an_answer(self) -> None:
        """Data, not behaviour. A source that also walked the graph is the defect
        this seam was carved to prevent, and it shipped once already."""
        banned = ("search", "recall", "recall_facts", "spreading_activation",
                  "activate", "score", "rank")
        for name in banned:
            assert not hasattr(CozoAdjacencySource, name), (
                f"{name} is back on the adjacency source. The walk lives once, "
                "in retrieval/spreading, as a pure function of a snapshot."
            )


class TestItRefusesRatherThanAnswerShort:
    def test_global_scope_is_declined(self) -> None:
        """The projection stores one profile per edge, so it cannot see global
        memories. A partial answer would shrink the graph around a candidate."""
        client = _Client([["a", "b", 1.0]])
        source = CozoAdjacencySource(_Backend(client))

        assert source.edges("default", include_global=True) is None
        assert client.calls == [], "it must not even ask"

    def test_shared_scope_is_declined(self) -> None:
        client = _Client([["a", "b", 1.0]])
        source = CozoAdjacencySource(_Backend(client))

        assert source.edges("default", include_shared=True) is None
        assert client.calls == []

    def test_an_unreadable_projection_declines_instead_of_raising(self) -> None:
        """SQLite can always answer this, so a projection error is not fatal --
        but it must be a decline the caller can see, not an empty edge list that
        silently produces a graph with no neighbours in it."""
        source = CozoAdjacencySource(_Backend(_Client(raises=True)))

        assert source.edges("default") is None

    def test_a_backend_with_no_client_declines(self) -> None:
        class Bare:
            pass

        assert CozoAdjacencySource(Bare()).edges("default") is None


class TestTheChannelUsesItWhenItIsThere:
    """The wiring, not just the adapter."""

    def test_the_channel_reads_the_projection_and_records_the_source(
        self, monkeypatch,
    ) -> None:
        from superlocalmemory.graph import cozo_adjacency
        from superlocalmemory.retrieval import entity_channel as channel_module

        seen: dict[str, object] = {}

        class _Source:
            name = "cozo"

            def edges(self, profile_id, *, include_global=False, include_shared=False):
                seen["profile_id"] = profile_id
                return [("fact-a", "fact-b", 0.75)]

        monkeypatch.setattr(cozo_adjacency, "adjacency_source", lambda: _Source())

        db = _StubDb()
        ch = channel_module.EntityGraphChannel(db)
        ch._ensure_adjacency("default")

        assert seen["profile_id"] == "default"
        assert ch._adjacency_source_name == "cozo"
        assert db.edge_queries == [], (
            "the channel fetched edges from SQLite anyway; the projection read "
            "was wasted work on top of the read it was meant to replace"
        )

    def test_the_channel_falls_back_to_sqlite_when_the_source_declines(
        self, monkeypatch,
    ) -> None:
        from superlocalmemory.graph import cozo_adjacency
        from superlocalmemory.retrieval import entity_channel as channel_module

        class _Declining:
            name = "cozo"

            def edges(self, profile_id, *, include_global=False, include_shared=False):
                return None

        monkeypatch.setattr(cozo_adjacency, "adjacency_source", lambda: _Declining())

        db = _StubDb()
        ch = channel_module.EntityGraphChannel(db)
        ch._ensure_adjacency("default")

        assert ch._adjacency_source_name == "sqlite"
        assert db.edge_queries, "a decline must send the channel to SQLite"


class _StubDb:
    """The smallest thing ``_ensure_adjacency`` needs, recording what it asked."""

    def __init__(self) -> None:
        self.edge_queries: list[str] = []

    def execute(self, sql: str, params: tuple = ()) -> list:
        # Only the edge FETCH counts. The channel still asks SQLite for an edge
        # COUNT to decide whether its cache is stale, and that stays where it is:
        # it is an indexed aggregate, and the projection is not the authority on
        # how many edges the store has.
        if "source_id, target_id, weight" in sql:
            self.edge_queries.append(sql)
        return []

    def get_fact_count(self, profile_id, **kwargs) -> int:
        return 0

    @staticmethod
    def visible_fact_clause(*_args, **_kwargs) -> str:
        return ""
