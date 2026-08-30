# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The list order is the recommendation; `score` is not the ordering key.

Ranking weighs things that are not properties of the query — whether a memory
has helped before, whether this session was just looking at it. So the top
result can carry a lower `score` than the one beneath it, and a caller that
sorts by `score` gets a different answer than the one it was given.

That is a real trap for a consumer, so the position has to be authoritative and
it has to reach the wire. These pin both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from superlocalmemory.core.score_contract import finalize_score_contract
from superlocalmemory.storage.models import RecallResponse


@dataclass
class _Fact:
    fact_id: str
    confidence: float = 0.9
    content: str = "something"
    memory_id: str = "m1"


@dataclass
class _Result:
    fact: _Fact
    score: float
    ranking_score: float | None = None
    relevance_score: float | None = None
    memory_confidence: float | None = None
    confidence: float | None = None
    rank_position: int = 0
    channel_scores: dict = field(default_factory=dict)


def test_rank_position_follows_the_list_order_not_the_score():
    """The position is what a consumer must trust."""
    response = RecallResponse(
        query="q",
        results=[
            # Ordered by ranking utility: the boosted one leads on a lower score.
            _Result(fact=_Fact("boosted"), score=0.55, ranking_score=0.75),
            _Result(fact=_Fact("stronger"), score=0.72, ranking_score=0.72),
        ],
    )
    finalize_score_contract(response)

    assert [r.rank_position for r in response.results] == [1, 2]
    assert response.results[0].fact.fact_id == "boosted"
    # And the disagreement really is present, so this is not a vacuous pass.
    assert response.results[0].score < response.results[1].score


def test_finalizing_does_not_reorder():
    """Re-sorting here would silently undo every ranking pass before it."""
    response = RecallResponse(
        query="q",
        results=[
            _Result(fact=_Fact("first"), score=0.10, ranking_score=0.99),
            _Result(fact=_Fact("second"), score=0.90, ranking_score=0.10),
        ],
    )
    finalize_score_contract(response)
    assert [r.fact.fact_id for r in response.results] == ["first", "second"]


def test_the_position_reaches_the_wire():
    """A consumer cannot honour an order it is never told."""
    from superlocalmemory.server.recall_serializer import (
        recall_response_metadata,
        serialize_recall_response,
    )

    response = RecallResponse(
        query="q",
        results=[
            _Result(fact=_Fact("boosted"), score=0.55, ranking_score=0.75),
            _Result(fact=_Fact("stronger"), score=0.72, ranking_score=0.72),
        ],
    )
    finalize_score_contract(response)
    results, _ = serialize_recall_response(
        response, limit=10, memory_map={"m1": "something"},
        per_fact_max=2400, total_max=12000,
    )
    assert results, "nothing serialised"
    assert [r.get("rank_position") for r in results] == [1, 2]
    # ranking_score travels too, so a caller that wants to re-derive the order
    # has the field that actually produced it.
    assert results[0].get("ranking_score") is not None
    assert recall_response_metadata(response) is not None
