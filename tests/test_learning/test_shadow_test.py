# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""ShadowTest — independent-groups, always-valid promotion test (BRN-01).

The old validator enforced one arm per query id and then paired the two arms
on the intersection of their query ids, which is always empty: 4,000 routed
observations of a perfect candidate still returned ``continue``. These tests
prove both terminal decisions are reachable from routed (disjoint) traffic,
that replays are idempotent and survive a restart, and — by running the exact
production decision rule under continuous monitoring — that an A/A experiment
promotes no more often than ``ALPHA``.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.learning.shadow_test import ShadowTest, msprt_lambda


def _feed(st: ShadowTest, rng: random.Random, p_active: float,
          p_candidate: float, n: int, prefix: str = "q") -> str:
    """Route ``n`` queries like production and stop at the first verdict."""
    decision = "continue"
    for i in range(n):
        qid = f"{prefix}-{i}"
        arm = st.route_query(qid)
        p = p_candidate if arm == "candidate" else p_active
        st.record_observation(query_id=qid, arm=arm,
                              reward=1.0 if rng.random() < p else 0.0)
        decision, _ = st.decide()
        if decision != "continue":
            break
    return decision


def test_the_audit_reproduction_now_promotes() -> None:
    """Perfect candidate (reward 1) vs useless active (reward 0), routed."""
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    assert _feed(st, random.Random(1), 0.0, 1.0, 4000) == "promote"
    decision, stats = st.decide()
    assert decision == "promote"
    assert stats["criterion"] == "msprt_candidate_better"
    assert stats["n_active"] >= ShadowTest.MIN_PER_ARM
    assert stats["n_candidate"] >= ShadowTest.MIN_PER_ARM


def test_a_worse_candidate_is_rejected() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    assert _feed(st, random.Random(2), 0.6, 0.3, 4000) == "reject"
    assert st.decide()[1]["criterion"] == "msprt_candidate_worse"


def test_no_difference_is_rejected_at_the_horizon() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    rng = random.Random(3)
    for i in range(4 * ShadowTest.MAX_PER_ARM):
        qid = f"h-{i}"
        st.record_observation(query_id=qid, arm=st.route_query(qid),
                              reward=0.5)
    decision, stats = st.decide()
    assert decision == "reject"
    assert stats["criterion"] == "horizon_without_win"
    del rng


def test_nothing_is_decided_before_the_minimum_per_arm() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    for i in range(ShadowTest.MIN_PER_ARM - 1):
        st.record_observation(query_id=f"a-{i}", arm="active", reward=0.0)
        st.record_observation(query_id=f"c-{i}", arm="candidate", reward=1.0)
    decision, stats = st.decide()
    assert decision == "continue"
    assert stats["criterion"] == "accumulating"


def test_a_replayed_query_id_counts_once_whichever_arm_it_claims() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    assert st.record_observation(query_id="q", arm="active", reward=0.0)
    assert not st.record_observation(query_id="q", arm="active", reward=1.0)
    assert not st.record_observation(query_id="q", arm="candidate", reward=1.0)
    _, stats = st.decide()
    assert (stats["n_active"], stats["n_candidate"]) == (1, 0)


def test_observations_survive_a_restart_and_stay_deduplicated(
        tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    from superlocalmemory.storage.migrations import M012_shadow_observations
    with sqlite3.connect(db) as conn:
        conn.executescript(M012_shadow_observations.DDL)
    first = ShadowTest(profile_id="p", candidate_model_id="7",
                       learning_db=str(db))
    for i in range(40):
        first.record_observation(query_id=f"a{i}", arm="active", reward=0.0)
        first.record_observation(query_id=f"c{i}", arm="candidate", reward=1.0)
    first.record_observation(query_id="a0", arm="candidate", reward=1.0)
    again = ShadowTest(profile_id="p", candidate_model_id="7",
                       learning_db=str(db))
    assert again.decide() == first.decide()
    assert again.decide()[0] == "promote"
    assert not again.record_observation(query_id="c3", arm="candidate",
                                        reward=0.0)


def test_legacy_alias_still_records() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    assert st.record_recall_pair(query_id="x", arm="active", ndcg_at_10=0.4)
    assert st.decide()[1]["mean_active"] == pytest.approx(0.4)


def test_route_is_deterministic_sha256_and_balanced() -> None:
    st = ShadowTest(profile_id="p", candidate_model_id="1")
    for qid in ("query-1", "abc", "x" * 40):
        h = hashlib.sha256(qid.encode()).hexdigest()[:8]
        want = "candidate" if int(h, 16) % 2 else "active"
        assert st.route_query(qid) == st.route_query(qid) == want
    counts = {"active": 0, "candidate": 0}
    for i in range(1000):
        counts[st.route_query(f"q-{i}")] += 1
    assert 400 <= counts["active"] <= 600


def test_lambda_matches_the_equal_arm_two_stream_form() -> None:
    """With n per arm, V = 2 s^2 / n — Johari et al. (22) with H=N(0, g^2)."""
    import math
    n, s2, g, d = 100, 0.25, 0.1, 0.07
    v = 2 * s2 / n
    expect = math.sqrt(v / (v + g * g)) * math.exp(
        g * g * d * d / (2 * v * (v + g * g)))
    assert msprt_lambda(d, v, g) == pytest.approx(expect)
    assert msprt_lambda(5.0, 1e-9, g) == math.inf  # no overflow


@pytest.mark.parametrize("p", [0.5, 0.2])
def test_false_promotion_rate_under_continuous_monitoring(p: float) -> None:
    """A/A: the exact production rule, evaluated after EVERY observation.

    Bernoulli rewards are the least normal case the test can see. 300
    replicates; the observed promote rate must be below ALPHA. (Measured
    while writing this: 1.3% at p=0.5 and 1.7% at p=0.2, 600 replicates.)
    """
    rng = random.Random(11)
    promotes = 0
    runs = 300
    for r in range(runs):
        st = ShadowTest(profile_id="p", candidate_model_id="1")
        if _feed(st, rng, p, p, 4 * ShadowTest.MAX_PER_ARM,
                 prefix=f"r{r}") == "promote":
            promotes += 1
    assert promotes / runs < ShadowTest.ALPHA


def test_power_for_a_ten_point_lift() -> None:
    rng = random.Random(12)
    wins = sum(
        _feed(ShadowTest(profile_id="p", candidate_model_id="1"), rng,
              0.5, 0.6, 4 * ShadowTest.MAX_PER_ARM, prefix=f"w{r}") == "promote"
        for r in range(60)
    )
    assert wins / 60 >= 0.8
