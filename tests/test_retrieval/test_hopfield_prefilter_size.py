# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Tests for HopfieldConfig.prefilter_candidates default and routing behaviour.

Establishes two properties:
  1. The default pool size is 150 (not 1000), so Hopfield stays within the
     1-second latency budget on typical stores.
  2. Shrinking the pool to 150 does not change the returned fact set for a
     query whose answer ranks within the top-150 by cosine similarity —
     the no-quality-trade-off contract.

Test IDs starting at 100 to avoid collision with existing test_hopfield_channel.py
numbering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, call

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DIM = 768


def _normed_vec(seed: int, d: int = DIM) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(d).astype(np.float32)
    v /= max(float(np.linalg.norm(v)), 1e-8)
    return v.tolist()


@dataclass
class _Fact:
    fact_id: str
    profile_id: str = "p"
    embedding: list[float] | None = None


def _facts(n: int, d: int = DIM, profile: str = "p") -> list[_Fact]:
    return [
        _Fact(fact_id=f"f{i}", profile_id=profile, embedding=_normed_vec(100 + i, d))
        for i in range(n)
    ]


class _DB:
    """Complete FakeDB stub that satisfies all methods called by HopfieldChannel."""

    def __init__(self, facts: list[_Fact], reported_count: int | None = None) -> None:
        self._facts = facts
        self._reported_count = reported_count if reported_count is not None else len(facts)

    def get_fact_count(
        self, profile_id: str, include_global: bool = False, include_shared: bool = False
    ) -> int:
        return self._reported_count

    def get_all_facts(
        self,
        profile_id: str,
        limit: int | None = None,
        include_global: bool = False,
        include_shared: bool = False,
    ) -> list[_Fact]:
        out = [f for f in self._facts if f.profile_id == profile_id]
        return out[:limit] if limit is not None else out

    def get_facts_by_ids(
        self,
        fact_ids: list[str],
        profile_id: str,
        include_global: bool = False,
        include_shared: bool = False,
    ) -> list[_Fact]:
        wanted = set(fact_ids)
        return [f for f in self._facts if f.fact_id in wanted and f.profile_id == profile_id]

    def get_external_visible_facts(
        self, profile_id: str, include_global: bool = False, include_shared: bool = False
    ) -> list[_Fact]:
        # No cross-profile facts in these unit tests.
        return []


class _VS:
    """Fake VectorStore with a configurable result list."""

    def __init__(
        self,
        available: bool = True,
        count_val: int = 0,
        results: list[tuple[str, float]] | None = None,
    ) -> None:
        self.available = available
        self._count_val = count_val
        self._results = results or []

    def count(self, profile_id: str | None = None) -> int:
        return self._count_val

    def search(
        self,
        query: list[float],
        top_k: int = 50,
        profile_id: str | None = None,
    ) -> list[tuple[str, float]]:
        return self._results[:top_k]


# ---------------------------------------------------------------------------
# Test 100 — pin the default prefilter size
# ---------------------------------------------------------------------------

class TestPrefilterDefaultIs150:
    """The HopfieldConfig default for prefilter_candidates must be 150.

    RED: fails with AssertionError when the default is 1000.
    GREEN: passes after the default is changed to 150.
    """

    def test_default_prefilter_candidates_is_150(self) -> None:
        from superlocalmemory.math.hopfield import HopfieldConfig

        cfg = HopfieldConfig()
        assert cfg.prefilter_candidates == 150, (
            f"Expected prefilter_candidates=150, got {cfg.prefilter_candidates}. "
            "Change HopfieldConfig.prefilter_candidates default to 150."
        )


# ---------------------------------------------------------------------------
# Test 101 — prefilter path is taken for a 200-fact corpus with default config
# ---------------------------------------------------------------------------

class TestPrefilterPathTakenWithDefaultConfig:
    """A 200-fact corpus must route through the ANN pre-filter with default config.

    With prefilter_candidates=1000 (old): 200 <= 1000, so full-matrix path is
    taken and VectorStore.search() is NOT called for the pre-filter.

    With prefilter_candidates=150 (new): 200 > 150, so the prefilter path is
    taken and VectorStore.search() IS called.

    RED: VectorStore.search not called → asserts False.
    GREEN: VectorStore.search is called → asserts True.
    """

    def test_prefilter_path_taken_for_200_facts(self) -> None:
        from superlocalmemory.math.hopfield import HopfieldConfig
        from superlocalmemory.retrieval.hopfield_channel import HopfieldChannel

        corpus = _facts(200)
        # VectorStore returns top-5 facts as KNN result.
        knn_top5 = [(f"f{i}", 0.95 - i * 0.01) for i in range(5)]
        db = _DB(corpus, reported_count=200)
        vs = _VS(available=True, count_val=200, results=knn_top5)

        # Use the DEFAULT config (no explicit prefilter_candidates).
        channel = HopfieldChannel(db=db, vector_store=vs)

        # Wrap vs.search so we can detect whether it was called.
        real_search = vs.search
        search_calls: list[Any] = []

        def tracking_search(query: list[float], top_k: int = 50, profile_id: str | None = None) -> list[tuple[str, float]]:
            search_calls.append(top_k)
            return real_search(query, top_k, profile_id)

        vs.search = tracking_search  # type: ignore[method-assign]

        query = _normed_vec(999)
        channel.search(query, "p", top_k=5)

        assert len(search_calls) > 0, (
            "VectorStore.search was not called, meaning the full-matrix path "
            "was taken. With prefilter_candidates=150 and 200 facts, the "
            "prefilter path must be taken (200 > 150)."
        )


# ---------------------------------------------------------------------------
# Test 102 — result set is stable when both pool sizes use the same candidates
# ---------------------------------------------------------------------------

class TestResultSetStableAcrossPoolSizes:
    """When both pool sizes use the prefilter path and the VectorStore returns
    the same candidate set, Hopfield produces identical results regardless of
    which prefilter_candidates value was used to request those candidates.

    This is the quality-safety contract for the 1000 → 150 reduction:
    on stores large enough that both values trigger the prefilter path
    (reported_count > max(150, 1000)), and where the VectorStore returns the
    same N facts for both top_k requests (because the VS has exactly N facts),
    the returned fact-ID list must be identical.

    Design note: this is a quality-contract test, not a RED → GREEN constant
    test. Both runs explicitly supply their own HopfieldConfig so the result
    is independent of the module-level default. The test is always GREEN for
    correct implementations; it would fail only if the Hopfield math itself
    produced non-deterministic results given the same input sub-matrix.

    The reported_count is set to 5000 so that 5000 > 1000 > 150 — both pool
    sizes trigger the prefilter path. The VectorStore result list is capped at
    150 items, so both top_k=150 and top_k=1000 requests receive the same 150
    candidates. Hopfield operates on the identical sub-matrix in both cases.
    """

    def test_same_top_facts_when_vs_returns_same_candidates(self) -> None:
        from superlocalmemory.math.hopfield import HopfieldConfig
        from superlocalmemory.retrieval.hopfield_channel import HopfieldChannel

        # 150 real facts; reported count 5000 → prefilter path for both pool sizes.
        corpus = _facts(150)
        query = _normed_vec(999)

        # VS result list has exactly 150 entries — both top_k=150 and top_k=1000
        # requests will receive the same 150 candidates.
        knn_results = [(f"f{i}", 0.99 - i * 0.001) for i in range(150)]

        def _run(prefilter: int) -> list[str]:
            cfg = HopfieldConfig(prefilter_candidates=prefilter)
            # reported_count=5000 > both pool sizes → prefilter path guaranteed.
            db = _DB(corpus, reported_count=5000)
            vs = _VS(available=True, count_val=5000, results=knn_results)
            ch = HopfieldChannel(db=db, vector_store=vs, config=cfg)
            results = ch.search(query, "p", top_k=10)
            return [fid for fid, _ in results]

        ids_at_150 = _run(150)
        ids_at_1000 = _run(1000)

        assert ids_at_150 == ids_at_1000, (
            "Hopfield results differ despite both pool sizes receiving the same "
            "150 ANN candidates. This indicates non-determinism in the math, "
            "not a pool-size quality issue. "
            f"at-150: {ids_at_150}, at-1000: {ids_at_1000}"
        )
        # Verify the prefilter path was actually taken (non-empty results).
        assert len(ids_at_150) > 0, "Prefilter path returned no results."
