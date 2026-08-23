# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com | https://varunpratap.com

"""A Beta learner whose posterior never moved must be reported as such.

The failure this guards against is not a crash. A Thompson selector whose reward
channel emits one constant value keeps recording plays, keeps advancing its
timestamps, and keeps reporting a posterior — while the distribution stands
still. Its own counters cannot distinguish that from working, because they are
the thing that keeps moving.

The signature is exact rather than statistical. With a Beta(1,1) prior updated as
``alpha += r; beta += (1 - r)``, a reward of exactly 0.5 leaves
``alpha - 1 == beta - 1 == n/2`` for every n. 0.5 is exactly representable in
binary floating point, so the identity holds with no tolerance and one run is
enough to establish it.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.reliability import check_beta_learners


def _learning_db(path, arms):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE bandit_arms ("
        "  profile_id TEXT, stratum TEXT, arm_id TEXT PRIMARY KEY,"
        "  alpha REAL, beta REAL, plays INTEGER, last_played_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO bandit_arms (profile_id, stratum, arm_id, alpha, beta, plays,"
        " last_played_at) VALUES ('default','s',?,?,?,?,'2026-08-01T00:00:00Z')",
        arms,
    )
    conn.commit()
    conn.close()
    return path


class TestAStalledLearnerIsCaught:
    def test_every_reward_neutral_is_reported_as_stalled(self, tmp_path) -> None:
        """The production signature: n plays, posterior mean still 0.5."""
        db = _learning_db(
            tmp_path / "learning.db",
            [(f"arm{i}", 1.0 + n * 0.5, 1.0 + n * 0.5, n) for i, n in
             enumerate([16, 14, 14, 14, 14, 14])],
        )

        (verdict,) = check_beta_learners(db, min_observations=20)

        assert verdict.verdict == "STALLED"
        assert verdict.units == 6
        assert verdict.units_matching_neutral_identity == 6
        assert verdict.observations == 86
        assert "neutral" in verdict.detail.lower()

    def test_the_identity_is_exact_not_approximate(self, tmp_path) -> None:
        """One unit off the identity by a hair must not count as neutral.

        Guards the tolerance. If this check used a loose epsilon it would report
        a genuinely-learning arm as stalled, and a false positive here would
        train an operator to ignore it.
        """
        db = _learning_db(
            tmp_path / "learning.db",
            [("a", 1.0 + 30 * 0.5, 1.0 + 30 * 0.5, 30),
             ("b", 1.0 + 30 * 0.5 + 0.25, 1.0 + 30 * 0.5 - 0.25, 30)],
        )

        (verdict,) = check_beta_learners(db, min_observations=20)

        assert verdict.units_matching_neutral_identity == 1, (
            "the asymmetric arm satisfies no neutral identity"
        )
        assert verdict.units_at_prior_mean == 1
        assert verdict.verdict == "MOVING"


class TestALearningLearnerIsNotFlagged:
    """The control. A check that fires on a healthy learner is noise, and noise
    is how a real warning stops being read."""

    def test_asymmetric_posteriors_report_moving(self, tmp_path) -> None:
        db = _learning_db(
            tmp_path / "learning.db",
            [("a", 12.0, 3.0, 13), ("b", 4.0, 19.0, 21), ("c", 9.0, 9.0, 16)],
        )

        (verdict,) = check_beta_learners(db, min_observations=20)

        assert verdict.verdict == "MOVING"
        assert not verdict.is_stalled

    def test_a_cold_learner_is_not_accused(self, tmp_path) -> None:
        """Below the observation floor, sitting at the prior is correct."""
        db = _learning_db(
            tmp_path / "learning.db", [("a", 1.5, 1.5, 1), ("b", 2.0, 2.0, 2)],
        )

        (verdict,) = check_beta_learners(db, min_observations=20)

        assert verdict.verdict == "INSUFFICIENT_DATA"
        assert not verdict.is_stalled


class TestItNeverBreaksTheCallerIt:
    """A diagnostic must never be the reason something fails."""

    @pytest.mark.parametrize("target", ["missing.db", "", "/nonexistent/x.db"])
    def test_an_unreadable_store_yields_no_verdicts(self, tmp_path, target) -> None:
        assert check_beta_learners(tmp_path / target if target else target) == []

    def test_a_table_without_beta_columns_is_skipped(self, tmp_path) -> None:
        path = tmp_path / "learning.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE bandit_arms (arm_id TEXT PRIMARY KEY, hits INT)")
        conn.commit()
        conn.close()

        assert check_beta_learners(path) == []
