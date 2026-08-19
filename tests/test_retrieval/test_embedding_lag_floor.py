# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A fact whose vector has not been computed yet still gets a fair hearing.

Fusion is rank-based, so a fact the semantic channel did not return forfeits that
channel's whole contribution — the heaviest one. When the reason is that the
vector has not been computed yet, that absence describes the ingest pipeline and
says nothing about the fact, and a memory written seconds ago becomes the hardest
thing in the store to find.

Such candidates are placed at the MEDIAN of the semantic ranking, never near the
top. A candidate that HAS a vector and simply was not returned is left alone: that
absence is real evidence, and the two must not be conflated.

Scope, stated plainly because it was measured and is easy to overstate: this is a
safety net, not a cure. It lifts a fact that other channels already retrieved. It
cannot help a fact that no channel retrieved at all, which is what happens when a
question is phrased naturally rather than quoting the fact's own wording. Making
that case work requires the vector to exist at write time; see the release notes
for this task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from superlocalmemory.core.config import RetrievalConfig
from superlocalmemory.retrieval.engine import RetrievalEngine


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def _engine(unenriched: list[str], *, config: RetrievalConfig | None = None) -> RetrievalEngine:
    """Engine whose store reports exactly `unenriched` as lacking a vector.

    The production query asks for candidates that are inside the write window AND
    have no row in the vector-projection table. The stub answers with whatever the
    test declares, so each test states its own premise instead of building a store.
    """
    db = MagicMock()
    db.execute.return_value = [{"fact_id": fid} for fid in unenriched]
    db.get_facts_by_ids.return_value = []
    db.get_scenes_for_facts_batch.return_value = {}
    db.get_invalidated_fact_ids.return_value = set()
    db.get_nonapplied_correction_successor_ids.return_value = set()
    db.get_strict_temporal_excluded_fact_ids.return_value = set()
    return RetrievalEngine(
        db=db, config=config or RetrievalConfig(), channels={},
        embedder=MagicMock(), reranker=None,
    )


def _semantic_order(result: dict[str, list[tuple[str, float]]]) -> list[str]:
    return [fid for fid, _ in result["semantic"]]


class TestAdmittedAtTheMedian:
    """An un-enriched recent candidate competes, and does not win by default."""

    def test_it_is_admitted_to_the_semantic_ranking(self) -> None:
        ch = {
            "semantic": [(f"s{i}", 0.9 - i * 0.05) for i in range(6)],
            "bm25": [("fresh", 50.0)],
        }
        out = _engine(["fresh"])._semantic_rank_for_unenriched(ch)
        assert "fresh" in _semantic_order(out), (
            "a candidate with no vector yet was left out of the semantic ranking "
            "entirely, so it forfeits the most heavily weighted channel"
        )

    def test_it_lands_in_the_middle_not_at_the_top(self) -> None:
        """Placement is the whole design: enough to compete, not enough to win.

        Admitting it at the top would promote anything recent over everything
        established, which is the opposite failure and a worse one.
        """
        ch = {
            "semantic": [(f"s{i}", 0.9 - i * 0.05) for i in range(6)],
            "bm25": [("fresh", 50.0)],
        }
        order = _semantic_order(_engine(["fresh"])._semantic_rank_for_unenriched(ch))
        position = order.index("fresh")
        assert position != 0, "an un-enriched fact must not be handed the top semantic rank"
        assert position == 3, f"expected the median of 6, got position {position}"

    def test_the_original_ranking_is_not_mutated(self) -> None:
        ch = {"semantic": [("s0", 0.9), ("s1", 0.8)], "bm25": [("fresh", 50.0)]}
        before = list(ch["semantic"])
        _engine(["fresh"])._semantic_rank_for_unenriched(ch)
        assert ch["semantic"] == before, "the input mapping was modified in place"


class TestAbsenceThatIsRealEvidence:
    """A fact that HAS a vector and still was not returned is left alone."""

    def test_enriched_candidate_is_not_admitted(self) -> None:
        """The distinction this whole mechanism rests on.

        If a fact has a vector and the semantic channel still did not return it,
        that is a genuine statement about relevance. Admitting it anyway would
        promote unrelated facts for being recent.
        """
        ch = {"semantic": [("s0", 0.9), ("s1", 0.8)], "bm25": [("unrelated", 12.0)]}
        # the store reports nothing as un-enriched: `unrelated` has its vector
        out = _engine([])._semantic_rank_for_unenriched(ch)
        assert _semantic_order(out) == ["s0", "s1"], (
            "a fact with a vector was admitted to the semantic ranking; absence "
            "of a semantic hit is evidence for that fact and must be respected"
        )

    def test_a_candidate_the_store_excludes_is_not_admitted(self) -> None:
        """Window and vector membership are decided by the query, not the caller."""
        ch = {"semantic": [("s0", 0.9)], "bm25": [("old", 12.0), ("fresh", 30.0)]}
        out = _engine(["fresh"])._semantic_rank_for_unenriched(ch)
        order = _semantic_order(out)
        assert "fresh" in order and "old" not in order


class TestItStaysOutOfTheWay:
    def test_disabled_by_configuration(self) -> None:
        cfg = RetrievalConfig()
        cfg = type(cfg)(**{**cfg.__dict__, "write_recency_floor_enabled": False}) \
            if hasattr(cfg, "__dict__") else cfg
        eng = _engine(["fresh"], config=cfg)
        ch = {"semantic": [("s0", 0.9)], "bm25": [("fresh", 50.0)]}
        if getattr(cfg, "write_recency_floor_enabled", True) is False:
            assert eng._semantic_rank_for_unenriched(ch) is ch

    def test_disabled_by_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLM_WRITE_RECENCY_NO_FLOOR", "1")
        ch = {"semantic": [("s0", 0.9)], "bm25": [("fresh", 50.0)]}
        assert _engine(["fresh"])._semantic_rank_for_unenriched(ch) is ch

    def test_no_semantic_channel_is_a_no_op(self) -> None:
        """With nothing to be ranked among, there is no median to occupy."""
        ch = {"bm25": [("fresh", 50.0)]}
        assert _engine(["fresh"])._semantic_rank_for_unenriched(ch) is ch

    def test_no_new_candidates_is_a_no_op(self) -> None:
        ch = {"semantic": [("s0", 0.9)], "bm25": [("s0", 12.0)]}
        assert _engine([])._semantic_rank_for_unenriched(ch) is ch


class TestFailureIsVisible:
    def test_a_store_error_does_not_break_ranking(self) -> None:
        """Ranking must still return if this refinement cannot run."""
        eng = _engine([])
        eng._db.execute.side_effect = RuntimeError("database is locked")
        ch = {"semantic": [("s0", 0.9)], "bm25": [("fresh", 50.0)]}
        assert eng._semantic_rank_for_unenriched(ch) == ch

    def test_a_coding_error_is_not_swallowed(self) -> None:
        """A broad except here once hid a missing import and left this inert.

        Every test passed while the feature did nothing at all, so the distinction
        between "the data is unusual" and "this code is wrong" is pinned.
        """
        eng = _engine([])
        eng._db.execute.side_effect = AttributeError("no such attribute")
        ch = {"semantic": [("s0", 0.9)], "bm25": [("fresh", 50.0)]}
        with pytest.raises(AttributeError):
            eng._semantic_rank_for_unenriched(ch)
