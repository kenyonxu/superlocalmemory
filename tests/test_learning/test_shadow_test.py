# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — Track A.3 (LLD-10 / LLD-00 §8)

"""Tests for ``superlocalmemory.learning.shadow_test`` (ShadowTest).

Contract references:
  - LLD-00 §8   — two-phase shadow test (Phase A n=100 early-stop,
                 Phase B n=885 full validation; α=0.05 paired t-test;
                 promotion criterion: MRR lift ≥ +0.02 with p<0.05).
  - LLD-10 §4.1 — deterministic A/B routing via SHA-256(query_id)
                 first 8 hex chars → 2-bucket modulo split.
  - LLD-10 §4.5 — promotion decision (paired t-test, fallback to
                 hardcoded t>2.0 if scipy absent).
  - IMPLEMENTATION-MANIFEST v3.4.21 FINAL A.3 — 5 test names verbatim.

Stdlib-only tests; no scipy/lightgbm required. The ShadowTest class is
pure bookkeeping + paired-t math — it does not touch the DB or any
learning artifacts. Tests exercise the state machine (Phase A→B→decide)
and the deterministic routing invariant.
"""

from __future__ import annotations

import hashlib
import random
from typing import Iterable

import pytest

from superlocalmemory.learning.shadow_test import ShadowTest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_pairs(
    st: ShadowTest,
    *,
    pairs: Iterable[tuple[float, float]],
    start_query_num: int = 0,
) -> int:
    """Record ``(active_score, candidate_score)`` pairs through ``st``.

    Returns number of pairs recorded.
    """
    count = 0
    for i, (active, candidate) in enumerate(pairs):
        qid_a = f"q-active-{start_query_num + i}"
        qid_c = f"q-candidate-{start_query_num + i}"
        st.record_recall_pair(query_id=qid_a, arm="active", ndcg_at_10=active)
        st.record_recall_pair(query_id=qid_c, arm="candidate", ndcg_at_10=candidate)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Manifest-locked tests
# ---------------------------------------------------------------------------


def test_shadow_phase_a_early_stop_on_strong_signal() -> None:
    """Phase A (n=100) early-stops when |effect| > 0.08 AND p<0.01.

    Seed a strong, low-variance lift; after Phase A's 100 pairs the
    decision should be ``promote`` without waiting for Phase B.
    """
    st = ShadowTest(profile_id="p", candidate_model_id="cand-1")
    rng = random.Random(0xA3)
    pairs: list[tuple[float, float]] = []
    for _ in range(st.PHASE_A_N):
        a = 0.40 + rng.gauss(0, 0.01)
        c = a + 0.12 + rng.gauss(0, 0.01)
        pairs.append((a, c))
    _record_pairs(st, pairs=pairs)
    decision, stats = st.decide()
    assert decision == "promote", f"expected promote, got {decision} ({stats})"
    assert stats.get("phase") == "A"
    assert abs(stats.get("effect", 0.0)) > 0.08


def test_shadow_phase_b_requires_885_pairs() -> None:
    """When Phase A is inconclusive but Phase B criterion met, promote.

    Set up a marginal effect (~+0.025) that does NOT trip Phase A's
    0.08 early-stop. Provide 885 paired recalls. Phase B must
    conclude promote.
    """
    st = ShadowTest(profile_id="p", candidate_model_id="cand-2")
    rng = random.Random(0xB5)
    pairs: list[tuple[float, float]] = []
    for _ in range(st.PHASE_B_N):
        a = 0.45 + rng.gauss(0, 0.15)
        c = a + 0.025 + rng.gauss(0, 0.015)
        pairs.append((a, c))
    _record_pairs(st, pairs=pairs)
    decision, stats = st.decide()
    assert decision == "promote", f"expected promote, got {decision} ({stats})"
    assert stats.get("phase") == "B"
    assert stats.get("n_pairs", 0) >= st.PHASE_B_N


def test_shadow_rejects_no_improvement() -> None:
    """Zero-effect (candidate == active) → reject after Phase B."""
    st = ShadowTest(profile_id="p", candidate_model_id="cand-3")
    rng = random.Random(0xCC)
    pairs: list[tuple[float, float]] = []
    for _ in range(st.PHASE_B_N):
        base = 0.5 + rng.gauss(0, 0.1)
        pairs.append((base, base + rng.gauss(0, 0.001)))
    _record_pairs(st, pairs=pairs)
    decision, stats = st.decide()
    assert decision == "reject", f"expected reject, got {decision} ({stats})"


def test_shadow_continues_when_inconclusive() -> None:
    """Below PHASE_B_N with marginal effect → decide returns 'continue'."""
    st = ShadowTest(profile_id="p", candidate_model_id="cand-4")
    rng = random.Random(0xEE)
    # Only Phase A (100) pairs with noisy marginal lift — not enough
    # for early-stop, not enough pairs for Phase B.
    pairs: list[tuple[float, float]] = []
    for _ in range(st.PHASE_A_N):
        a = 0.5 + rng.gauss(0, 0.2)
        c = a + 0.02 + rng.gauss(0, 0.2)
        pairs.append((a, c))
    _record_pairs(st, pairs=pairs)
    decision, stats = st.decide()
    assert decision == "continue", f"expected continue, got {decision} ({stats})"


def test_shadow_deterministic_ab_route_by_query_hash() -> None:
    """ShadowTest.route_query deterministically buckets via SHA-256.

    Same query_id MUST always bucket the same way. The split is defined
    by LLD-10 §4.1: ``int(sha256(qid)[:8], 16) % 2`` → 0='active',
    1='candidate'.
    """
    st = ShadowTest(profile_id="p", candidate_model_id="cand-5")

    def _expected(qid: str) -> str:
        h = hashlib.sha256(qid.encode("utf-8")).hexdigest()[:8]
        return "candidate" if int(h, 16) % 2 == 1 else "active"

    for qid in ("query-1", "query-2", "abc", "01HE...ULID", "x" * 40):
        a1 = st.route_query(qid)
        a2 = st.route_query(qid)
        assert a1 == a2, "route must be deterministic per query_id"
        assert a1 == _expected(qid), (
            f"route for {qid} ({a1}) must match SHA-256 bucket "
            f"({_expected(qid)})"
        )

    # Roughly balanced over a large sample (sanity — not a strict bound).
    counts = {"active": 0, "candidate": 0}
    for i in range(1000):
        counts[st.route_query(f"q-{i}")] += 1
    assert 400 <= counts["active"] <= 600
    assert 400 <= counts["candidate"] <= 600
