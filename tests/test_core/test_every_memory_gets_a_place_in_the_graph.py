# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Recall ranks a memory by where it sits in the graph, so every memory needs a place.

``fact_importance`` holds one PageRank score and one community per memory. Recall
multiplies a candidate's activation by ``min(1 + pagerank * 2, 2)`` at every hop
and then favours candidates sharing a community with the query's seeds. A memory
with no row is found by the walk and ranked as though it had no position in the
graph at all -- which is not the same claim as "it has no neighbours", and the
ranker cannot tell the two apart.

Measured on a copy of the author's store before this work: 1,036 of 4,034 visible
memories had no score and no community, the whole table had last been computed
nine days earlier, and nothing scheduled it. Worse, a second writer had put ten
rows in at 0.1 -- eleven times the largest real score, and enough mass that the
table summed to 1.9999 instead of 1.

So these tests pin three things: **every visible memory gets a row, only visible
memories get rows, and the same store computes the same numbers twice.**
"""

from __future__ import annotations

import pytest

from superlocalmemory.core.graph_metrics import (
    compute_graph_metrics,
    metrics_are_stale,
)
from superlocalmemory.storage.models import (
    AtomicFact,
    EdgeType,
    GraphEdge,
    MemoryRecord,
)

_PARENT = "m-graph-metrics"


@pytest.fixture
def db(engine_with_mock_deps):
    """A live store with the parent memory every hand-built fact belongs to."""
    manager = engine_with_mock_deps._db
    manager.store_memory(MemoryRecord(
        memory_id=_PARENT, profile_id="default",
        content="parent record for the graph-metrics tests",
    ))
    return manager


def _fact(db, content: str, profile_id: str = "default") -> str:
    return db.store_fact(AtomicFact(
        memory_id=_PARENT, profile_id=profile_id, content=content,
    ))


def _edge(db, source: str, target: str, weight: float = 1.0) -> None:
    db.store_edge(GraphEdge(
        source_id=source, target_id=target, edge_type=EdgeType.SEMANTIC,
        weight=weight, profile_id="default",
    ))


def _rows(db, profile_id: str = "default") -> dict[str, dict]:
    return {
        dict(r)["fact_id"]: dict(r)
        for r in db.execute(
            "SELECT * FROM fact_importance WHERE profile_id = ?", (profile_id,),
        )
    }


def _chain(db, count: int) -> list[str]:
    """A connected line of facts, so PageRank has something to distribute."""
    ids = [_fact(db, f"chained memory number {i} about graph metrics") for i in range(count)]
    for left, right in zip(ids, ids[1:]):
        _edge(db, left, right)
    return ids


class TestEveryVisibleMemoryGetsARow:
    """Coverage is the property. A missing row is a mis-ranked memory."""

    def test_a_fact_with_no_edges_still_gets_a_row(self, db) -> None:
        """The defect: isolated facts were skipped, so they looked uncomputed.

        The previous pass wrote one row per node of a graph built from the edge
        tables. A memory with no edges was not a node, so it got nothing -- and
        "no row" is what the ranker also sees for a memory stored one second ago
        that the pass has not reached. Identical input, two very different
        meanings.
        """
        connected = _chain(db, 3)
        lonely = _fact(db, "a memory with no relationships to anything at all")

        report = compute_graph_metrics(db, "default")

        rows = _rows(db)
        assert report.ok, report.error
        assert lonely in rows, "an isolated memory must still be scored"
        assert rows[lonely]["pagerank_score"] > 0.0
        assert all(fid in rows for fid in connected)
        assert report.isolated >= 1
        assert report.connected >= 3

    def test_an_isolated_fact_gets_the_teleport_share_not_a_neighbours_score(
        self, db,
    ) -> None:
        """Its score is the value PageRank gives a node nothing links to.

        Not zero -- zero would mean "never reachable" and the boost formula would
        treat it as maximally unimportant. Not a neighbour's, either: an earlier
        segment-maximum bug in the walk handed empty rows the value at their start
        offset, and this is the same class of mistake one layer down.
        """
        _chain(db, 4)
        lonely = _fact(db, "an entirely unconnected observation about nothing")

        compute_graph_metrics(db, "default", damping=0.85)

        rows = _rows(db)
        total = len(rows)
        expected = (1.0 - 0.85) / total
        assert rows[lonely]["pagerank_score"] == pytest.approx(expected, rel=1e-6)
        assert rows[lonely]["community_id"] is None, (
            "a memory with no neighbours belongs to no community, and saying it "
            "belongs to one would earn it a bias it has not got"
        )

    def test_the_table_carries_one_unit_of_rank_in_total(self, db) -> None:
        """PageRank is a probability distribution. It sums to one, or it is not one.

        The second writer put ten rows in at 0.1 each and the table summed to
        1.9999 -- meaning those ten memories carried as much rank as the other
        2,988 combined. A sum check is the cheapest possible detector for that
        whole family of defect.
        """
        _chain(db, 6)
        _fact(db, "one more memory, deliberately unconnected")

        compute_graph_metrics(db, "default")

        total = sum(row["pagerank_score"] for row in _rows(db).values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_degree_centrality_reflects_the_edges_that_exist(self, db) -> None:
        """A hub scores above a leaf, or the column means nothing."""
        ids = _chain(db, 5)
        hub = ids[2]
        extra = [_fact(db, f"spoke memory {i}") for i in range(3)]
        for spoke in extra:
            _edge(db, hub, spoke)

        compute_graph_metrics(db, "default")

        rows = _rows(db)
        assert rows[hub]["degree_centrality"] > rows[ids[0]]["degree_centrality"]


class TestOnlyVisibleMemoriesGetRows:
    """The table is a projection of what recall may return, not of what is stored."""

    def test_a_withheld_fact_is_not_ranked(self, db) -> None:
        """It cannot be returned, so ranking it dilutes every real score.

        On the author's store the previous pass read ``atomic_facts`` raw and so
        ranked 1,299 facts the store refuses to hand back. Every real memory's
        share of the unit of rank was smaller by exactly that much.
        """
        ids = _chain(db, 4)
        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?",
            (ids[1],),
        )

        report = compute_graph_metrics(db, "default")

        rows = _rows(db)
        assert report.ok, report.error
        assert ids[1] not in rows, "a withheld memory must not be ranked"
        assert ids[0] in rows

    def test_withholding_a_fact_removes_the_row_it_already_had(self, db) -> None:
        """A stale row keeps the ranker scoring something recall will never return."""
        ids = _chain(db, 4)
        compute_graph_metrics(db, "default")
        assert ids[1] in _rows(db)

        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?",
            (ids[1],),
        )
        report = compute_graph_metrics(db, "default")

        assert ids[1] not in _rows(db)
        assert report.removed >= 1

    def test_one_profiles_pass_does_not_touch_another_profiles_rows(
        self, db,
    ) -> None:
        """Profiles are isolated everywhere else; this table is not an exception."""
        db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
            ("other", "Other"),
        )
        db.store_memory(MemoryRecord(
            memory_id="m-other", profile_id="other", content="other profile parent",
        ))
        mine = _chain(db, 3)
        theirs = db.store_fact(AtomicFact(
            memory_id="m-other", profile_id="other",
            content="a memory belonging to a different profile entirely",
        ))
        compute_graph_metrics(db, "other")
        assert theirs in _rows(db, "other")

        compute_graph_metrics(db, "default")

        assert theirs in _rows(db, "other"), (
            "recomputing one profile deleted another profile's metrics"
        )
        assert all(fid in _rows(db) for fid in mine)


class TestTheSameStoreAnswersTheSameWayTwice:
    """A recall that changes answer between runs is a quality defect, not a tie."""

    def test_two_passes_over_an_unchanged_store_agree_exactly(self, db) -> None:
        _chain(db, 8)
        _fact(db, "an unconnected memory to make the node set larger")

        compute_graph_metrics(db, "default")
        first = {k: (v["pagerank_score"], v["community_id"]) for k, v in _rows(db).items()}
        compute_graph_metrics(db, "default")
        second = {k: (v["pagerank_score"], v["community_id"]) for k, v in _rows(db).items()}

        assert first == second


class TestTheReportSaysWhatHappened:
    """A pass that wrote nothing and a store with no memories are different events."""

    def test_an_empty_profile_is_reported_as_empty_not_as_failure(self, db) -> None:
        report = compute_graph_metrics(db, "profile-that-has-nothing")
        assert report.ok
        assert report.written == 0
        assert "no visible facts" in report.notes

    def test_a_store_that_cannot_be_read_is_reported_as_an_error(self) -> None:
        """The previous version returned ``node_count: 0`` for this, silently."""
        class Unusable:
            pass

        report = compute_graph_metrics(Unusable(), "default")
        assert not report.ok
        assert report.error is not None
        assert report.written == 0


class TestStalenessIsAboutCoverageNotTheClock:
    """"Computed recently" says nothing about whether the store changed."""

    def test_a_new_memory_makes_the_metrics_stale(self, db) -> None:
        _chain(db, 3)
        compute_graph_metrics(db, "default")
        assert metrics_are_stale(db, "default")[0] is False

        _fact(db, "a memory stored after the last pass")

        stale, why = metrics_are_stale(db, "default")
        assert stale is True
        assert "no metrics" in why

    def test_a_row_for_a_memory_recall_cannot_return_makes_it_stale(
        self, db,
    ) -> None:
        ids = _chain(db, 3)
        compute_graph_metrics(db, "default")
        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?", (ids[0],),
        )

        stale, why = metrics_are_stale(db, "default")
        assert stale is True
        assert "cannot return" in why


class TestAMockIsNotAStore:
    """A test double must not be able to make the pass look like a no-op."""

    def test_a_db_that_only_fabricates_attributes_is_an_explicit_error(self) -> None:
        """MagicMock answers ``raw_connection`` and yields something unusable.

        Resolving the accessor on the instance therefore turned a readable store
        into "could not read the graph" wherever the suite passed a mock, and the
        report looked identical to a genuinely unreadable store. The lookup is on
        the type and the fallback is isinstance-checked; this pins that.
        """
        from unittest.mock import MagicMock

        report = compute_graph_metrics(MagicMock(), "default")

        assert not report.ok
        assert "no connection" in (report.error or "")
