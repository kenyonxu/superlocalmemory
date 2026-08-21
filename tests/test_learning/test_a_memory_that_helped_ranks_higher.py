# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""Ranking by what worked, without letting it eat the ranking.

Retrieval scores a memory by how much it RESEMBLES the query. Nothing in the
pipeline knew whether a memory had ever actually helped. PCOS is one number per
(fact, profile) holding the exponentially-weighted average of the rewards of the
settlements it took part in.

Three ways this goes wrong, one test class each:

* **It becomes a model feature.** An earlier design added ``"outcome_score"``
  to ``FEATURE_NAMES`` for inference and exclude it from the training matrix.
  That is a shape mismatch — ``booster.predict`` needs the columns the model was
  trained on — and ``features.py`` asserts ``len(FEATURE_NAMES) == FEATURE_DIM``
  with ``FEATURE_DIM = 20`` against a live 20-feature model. Applied after the
  model score instead, so that property holds by construction.
* **It overrules retrieval.** A memory that does not answer the question must
  not be dragged to the top by history.
* **It survives erasure.** A learned per-fact score is derived personal data.
  ``fact_expansion_fts`` already has exactly this defect; adding
  a second table with it in the same release would be indefensible.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.learning.pcos import (
    MAX_BONUS,
    bonus_for,
    confidence_weight,
    fetch_scores,
    update_scores,
)
from superlocalmemory.storage.migrations import M045_fact_outcome_score as _M045

_PROFILE = "default"


@pytest.fixture()
def store(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "memory.db"))
    conn.execute(
        "CREATE TABLE action_outcomes (outcome_id TEXT PRIMARY KEY,"
        " profile_id TEXT, fact_ids_json TEXT, reward REAL)"
    )
    _M045.apply(conn)
    conn.commit()
    return conn


class TestTheScoreMovesTowardWhatHappened:
    def test_a_success_raises_it_and_a_failure_lowers_it(self, store) -> None:
        update_scores(store, _PROFILE, ["f1"], 1.0)
        after_win = fetch_scores(store, _PROFILE, ["f1"])["f1"][0]
        assert after_win > 0.5

        update_scores(store, _PROFILE, ["f2"], 0.0)
        assert fetch_scores(store, _PROFILE, ["f2"])["f2"][0] < 0.5

    def test_it_converges_rather_than_swinging(self, store) -> None:
        """One outcome must nudge a score, not define it.

        A fact that helped once and failed once should end near neutral, not at
        whichever happened last.
        """
        update_scores(store, _PROFILE, ["f1"], 1.0)
        high = fetch_scores(store, _PROFILE, ["f1"])["f1"][0]
        update_scores(store, _PROFILE, ["f1"], 0.0)
        back = fetch_scores(store, _PROFILE, ["f1"])["f1"][0]
        assert back < high
        assert 0.4 < back < 0.6, back

    def test_repeated_success_accumulates(self, store) -> None:
        for _ in range(30):
            update_scores(store, _PROFILE, ["f1"], 1.0)
        score, plays = fetch_scores(store, _PROFILE, ["f1"])["f1"]
        assert plays == 30
        assert score > 0.6, score
        assert score <= 1.0

    def test_the_score_is_clamped(self, store) -> None:
        for _ in range(500):
            update_scores(store, _PROFILE, ["f1"], 1.0)
        score, _ = fetch_scores(store, _PROFILE, ["f1"])["f1"]
        assert 0.0 <= score <= 1.0

    def test_an_out_of_range_reward_cannot_poison_a_score(self, store) -> None:
        update_scores(store, _PROFILE, ["f1"], 42.0)
        assert fetch_scores(store, _PROFILE, ["f1"])["f1"][0] <= 1.0
        update_scores(store, _PROFILE, ["f2"], -8.0)
        assert fetch_scores(store, _PROFILE, ["f2"])["f2"][0] >= 0.0

    def test_a_cold_fact_reads_as_absent_not_as_bad(self, store) -> None:
        """Cold start must never look like a penalty."""
        assert fetch_scores(store, _PROFILE, ["never-seen"]) == {}
        assert bonus_for(0.5, 0) == 0.0


class TestItBreaksTiesWithoutOverrulingRetrieval:
    def test_an_unproven_fact_gets_no_bonus_either_way(self) -> None:
        assert bonus_for(0.99, 0) == 0.0
        assert bonus_for(0.01, 0) == 0.0

    def test_a_neutral_history_gets_no_bonus(self) -> None:
        """0.5 means "tried, and it made no difference"."""
        assert bonus_for(0.5, 1000) == 0.0

    def test_one_lucky_outcome_counts_for_little(self) -> None:
        once = bonus_for(0.9, 1)
        proven = bonus_for(0.9, 20)
        assert 0.0 < once < proven / 3, (once, proven)

    def test_confidence_saturates_rather_than_growing_forever(self) -> None:
        assert confidence_weight(20) == pytest.approx(1.0)
        assert confidence_weight(10_000) == pytest.approx(1.0)
        assert confidence_weight(0) == 0.0

    @pytest.mark.parametrize("score", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_the_bonus_is_hard_capped(self, score: float) -> None:
        """The ceiling is what stops history overruling relevance.

        Channel scores are [0, 1], so a 0.15 ceiling can reorder memories that
        already score similarly and cannot lift a poor match over a good one.
        """
        assert abs(bonus_for(score, 10_000)) <= MAX_BONUS + 1e-9


class TestItIsNotAModelFeature:
    def test_the_feature_vector_is_untouched(self) -> None:
        """Adding a 21st feature silently invalidates the live model."""
        from superlocalmemory.learning.features import (
            FEATURE_DIM,
            FEATURE_NAMES,
        )

        assert FEATURE_DIM == 20
        assert len(FEATURE_NAMES) == FEATURE_DIM
        assert "outcome_score" not in FEATURE_NAMES, (
            "PCOS entered the LightGBM feature vector; predict() and the "
            "trained model now disagree on shape, and the model can learn from "
            "its own output"
        )

    def test_it_is_applied_after_the_model_not_inside_it(self) -> None:
        import inspect

        from superlocalmemory.core import recall_pipeline

        src = inspect.getsource(recall_pipeline.apply_v2_bandit_ensemble)
        assert "_apply_outcome_bonus(final_results" in src, (
            "the outcome bonus is never applied, so PCOS is a column nobody "
            "reads"
        )
        assert src.index("ensemble_rerank(") < src.index(
            "_apply_outcome_bonus("
        ), "the bonus is applied before the model score, not after it"

    def test_the_bonus_reorders_something(self) -> None:
        """A bonus that cannot change an order is decoration.

        Two candidates a hair apart, one with a proven history: the proven one
        must come first afterwards.
        """
        from dataclasses import dataclass

        @dataclass
        class _Fact:
            fact_id: str

        @dataclass
        class _Result:
            fact: _Fact
            score: float
            ranking_score: float

        a = _Result(_Fact("a"), 0.80, 0.80)   # no history
        b = _Result(_Fact("b"), 0.78, 0.78)   # proven
        assert a.ranking_score > b.ranking_score
        b_bonus = bonus_for(0.95, 20)
        assert b.ranking_score + b_bonus > a.ranking_score, (
            "a proven memory cannot overtake a marginally better match, so "
            "the bonus can never change an answer"
        )


class TestErasureReachesIt:
    """Erasure must reach this table. profile_id is in the PK for this."""

    def test_the_table_is_discovered_as_profile_scoped(self, store) -> None:
        """``forget_profile`` finds tables by looking for a profile_id column.

        So the guarantee is structural, and this asserts the structure rather
        than trusting the docstring that describes it.
        """
        cols = {r[1] for r in store.execute(
            "PRAGMA table_info(fact_outcome_score)"
        )}
        assert "profile_id" in cols
        from superlocalmemory.compliance.gdpr import GDPRCompliance

        assert "fact_outcome_score" not in GDPRCompliance._NON_MEMORY_SCOPED

    def test_one_profiles_erasure_leaves_the_others_intact(self, store) -> None:
        update_scores(store, "alice", ["f1"], 0.9)
        update_scores(store, "bob", ["f1"], 0.9)
        store.commit()

        # What forget_profile does to every discovered profile-scoped table.
        store.execute(
            "DELETE FROM fact_outcome_score WHERE profile_id = ?", ("alice",),
        )
        store.commit()

        assert fetch_scores(store, "alice", ["f1"]) == {}
        assert fetch_scores(store, "bob", ["f1"]), (
            "erasing one profile took another profile's learned scores with it"
        )

    def test_the_primary_key_keeps_two_profiles_apart(self, store) -> None:
        """Without profile_id in the key these would be one row."""
        update_scores(store, "alice", ["f1"], 1.0)
        update_scores(store, "bob", ["f1"], 0.0)
        store.commit()
        alice = fetch_scores(store, "alice", ["f1"])["f1"][0]
        bob = fetch_scores(store, "bob", ["f1"])["f1"][0]
        assert alice > 0.5 > bob, (alice, bob)


class TestItDegradesRatherThanFailing:
    def test_ranking_survives_a_store_without_m045(self, tmp_path: Path) -> None:
        conn = sqlite3.connect(str(tmp_path / "bare.db"))
        assert fetch_scores(conn, _PROFILE, ["f1"]) == {}
        assert update_scores(conn, _PROFILE, ["f1"], 1.0) == 0

    def test_no_ids_is_not_a_query(self, store) -> None:
        assert fetch_scores(store, _PROFILE, []) == {}
        assert update_scores(store, _PROFILE, [], 1.0) == 0


class TestItDoesNotConcentrateOnAFewMemories:
    """The concentration limit, and the control that makes it mean something.

    "No fact reaches more than 5% of displays over a 1,000-query simulation."

    The risk is a feedback loop: a fact ranks well, gets shown, gets settled,
    ranks better. The simulation below drives that loop directly — the top
    result is settled from its own latent usefulness, which feeds back into the
    next query's ranking.

    THE PARAMETER THAT DECIDES EVERYTHING is the spread of retrieval scores. At
    a wide spread the bonus is irrelevant because base relevance dominates. At a
    narrow spread — which is what an embedding channel actually returns for
    closely related memories — a small consistent bonus decides every tie, and
    that is where concentration comes from. An earlier version of this
    simulation drew candidates uniformly and measured DISPLAY share, which is
    structurally capped at pool/n_facts = 5% and therefore could never fail. It
    passed at 33x the shipped ceiling, which is how it was caught.
    """

    @staticmethod
    def _simulate(*, bonus: bool, cap: bool, spread: float, seed: int,
                  n_facts: int = 200, n_queries: int = 1000,
                  pool: int = 10) -> float:
        import random

        from superlocalmemory.learning.pcos import TAU, RecentTopCounter

        rng = random.Random(seed)
        latent = {f: rng.random() for f in range(n_facts)}
        score = {f: 0.5 for f in range(n_facts)}
        plays = {f: 0 for f in range(n_facts)}
        rank1 = {f: 0 for f in range(n_facts)}
        counter = RecentTopCounter()

        for _ in range(n_queries):
            cand = rng.sample(range(n_facts), pool)
            base = {f: 0.8 + rng.uniform(-spread / 2, spread / 2) for f in cand}

            def total(f: int) -> float:
                if not bonus:
                    return base[f]
                if cap and counter.capped("p", str(f)):
                    return base[f]
                return base[f] + bonus_for(score[f], plays[f])

            top = max(cand, key=total)
            rank1[top] += 1
            counter.record_top("p", str(top))
            reward = 1.0 if rng.random() < latent[top] else 0.0
            played, old = plays[top], score[top]
            rate = TAU * min(1.0, played / 10.0) if played else TAU
            score[top] = (
                (1 - rate) * old + rate * reward if played
                else 0.5 + TAU * (reward - 0.5)
            )
            plays[top] = played + 1
        return max(rank1.values()) / n_queries

    _SEEDS = (11, 23, 37, 41, 59, 71, 83)

    def test_the_gate_condition_holds(self) -> None:
        worst = max(
            self._simulate(bonus=True, cap=True, spread=0.02, seed=s)
            for s in self._SEEDS
        )
        assert worst <= 0.05, (
            f"one memory took first place in {worst:.1%} of queries; the gate "
            "allows 5%"
        )

    def test_without_the_cap_it_would_not(self) -> None:
        """The control that stops the test above being a tautology.

        If this ever starts passing, the cap has become untestable here and the
        simulation needs sharpening — not the assertion relaxing.
        """
        worst = max(
            self._simulate(bonus=True, cap=False, spread=0.02, seed=s)
            for s in self._SEEDS
        )
        assert worst > 0.05, (
            f"the uncapped bonus concentrated only {worst:.1%}, so this "
            "simulation no longer exercises the risk the cap exists for"
        )

    def test_the_bonus_is_what_concentrates_not_the_tie_breaking(self) -> None:
        """Without this control, the cap could be solving a problem it doesn't have."""
        worst = max(
            self._simulate(bonus=False, cap=False, spread=0.02, seed=s)
            for s in self._SEEDS
        )
        assert worst < 0.02, (
            f"ranking concentrates at {worst:.1%} with NO bonus at all, so the "
            "5% gate is measuring the simulation and not the mechanism"
        )

    def test_the_cap_is_inert_when_scores_are_well_separated(self) -> None:
        """It must not tax the normal case to protect the pathological one."""
        for seed in self._SEEDS[:3]:
            uncapped = self._simulate(
                bonus=True, cap=False, spread=1.0, seed=seed,
            )
            capped = self._simulate(
                bonus=True, cap=True, spread=1.0, seed=seed,
            )
            assert capped == pytest.approx(uncapped, abs=0.005), (
                "the cap is changing rankings where base relevance already "
                "separates the candidates"
            )

    def test_a_capped_fact_keeps_its_retrieval_score(self) -> None:
        """Capping withholds the bonus. It must never demote.

        The original countermeasure multiplied a capped fact's ranking_score by
        0.1 — a 10x demotion for having been useful three times.
        """
        import inspect

        from superlocalmemory.core import recall_pipeline

        src = inspect.getsource(recall_pipeline._apply_outcome_bonus)
        assert "RECENT_TOPS.capped" in src
        assert "* 0.1" not in src and "*= 0.1" not in src, (
            "a capped fact is being demoted rather than merely not rewarded"
        )
