"""The walk gives one answer, whatever order the graph happened to arrive in.

Spreading multiplies an activation by ``decay * weight * boost`` at each hop.
While that product stays below 1 the result has a unique fixed point and the
order nodes are visited only changes how fast it is reached. At or above 1 an
activation can grow along a path, and then the order decides the answer.

That regime is not exotic. A small graph concentrates rank by construction, and
every workspace is small on its first day — measured at 1.1061 on a five-fact
workspace and 0.8497 on a twenty-four-fact one, against 0.7056 and 0.7100 on the
two large ones. So this is pinned by test rather than reasoned about in a
comment, which is what it was.
"""

from __future__ import annotations

import numpy as np
import pytest

from superlocalmemory.retrieval.graph_adjacency import (
    PAGERANK_BOOST_CAP,
    snapshot_from_maps,
)
from superlocalmemory.retrieval.spreading import activate

_DECAY = 0.7
_EDGES = [
    ("a", "b", 1.0), ("b", "c", 1.0), ("c", "d", 1.0),
    ("d", "e", 1.0), ("e", "a", 1.0),
]
_CONCENTRATED = {"a": 0.29, "b": 0.20, "c": 0.20, "d": 0.16, "e": 0.15}
_SPREAD = {name: 0.007 for name in "abcde"}


def _snapshot(rank_by_fact, *, reverse=False):
    """Build a snapshot, offering the graph in a chosen order."""
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for source, target, weight in _EDGES:
        adjacency.setdefault(source, []).append((target, weight))
        adjacency.setdefault(target, []).append((source, weight))
    names = list(rank_by_fact)
    if reverse:
        names.reverse()
        adjacency = {
            name: list(reversed(adjacency[name])) for name in reversed(list(adjacency))
        }
    return snapshot_from_maps(
        {name: adjacency.get(name, []) for name in names},
        {"seed": ["a"]},
        {"a": ["seed"]},
        {
            name: {"pagerank_score": rank_by_fact[name], "community_id": 0}
            for name in names
        },
        source="test", profile_id="default",
        nodes=set(names), fact_count=len(names),
    )


def test_a_small_graph_really_can_amplify():
    """Without this the ordering test below proves nothing — it would be
    checking that two runs agree in the regime where they always would."""
    peak = _snapshot(_CONCENTRATED).peak_propagation_factor(_DECAY)
    assert peak > 1.0, f"peak factor is {peak:.4f}; this graph does not amplify"
    assert peak == pytest.approx(
        _DECAY * min(1.0 + 2 * 0.29, PAGERANK_BOOST_CAP), rel=1e-6,
    )


def test_a_large_graph_does_not_amplify():
    """The other side of the same measurement, so the number means something."""
    peak = _snapshot(_SPREAD).peak_propagation_factor(_DECAY)
    assert peak < 1.0
    assert peak == pytest.approx(_DECAY * (1.0 + 2 * 0.007), rel=1e-6)


@pytest.mark.parametrize("ranks", [_CONCENTRATED, _SPREAD], ids=["amplifying", "not"])
def test_the_answer_does_not_depend_on_the_order_the_graph_arrived_in(ranks):
    forwards = _snapshot(ranks)
    backwards = _snapshot(ranks, reverse=True)

    assert list(forwards.node_ids) == list(backwards.node_ids), (
        "the two builds do not even agree on the order of the nodes, so any "
        "score read out of them is a score for a different node"
    )

    first = activate(forwards, ["seed"], decay=_DECAY, threshold=0.0, max_hops=3)
    second = activate(backwards, ["seed"], decay=_DECAY, threshold=0.0, max_hops=3)

    np.testing.assert_allclose(first.scores, second.scores, rtol=1e-12, atol=1e-15)


def test_the_rank_boost_never_exceeds_its_cap():
    """The cap is what bounds the factor at all. Without it the reasoning above
    is vacuous, because the factor could be anything."""
    absurd = {"a": 5.0, "b": 0.1, "c": 0.1, "d": 0.1, "e": 0.1}
    snapshot = _snapshot(absurd)
    boost = np.minimum(1.0 + snapshot.pagerank * 2.0, PAGERANK_BOOST_CAP)
    assert float(boost.max()) <= PAGERANK_BOOST_CAP
    assert snapshot.peak_propagation_factor(_DECAY) == pytest.approx(
        _DECAY * PAGERANK_BOOST_CAP, rel=1e-6,
    )
