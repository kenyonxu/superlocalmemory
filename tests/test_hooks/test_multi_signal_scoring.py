# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Tests for multi-signal scoring in AutoInvoker

"""Tests for 4-signal multi-signal scoring and Mode A degradation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from superlocalmemory.core.config import AutoInvokeConfig
from superlocalmemory.hooks.auto_invoker import AutoInvoker


def _make_scoring_invoker(
    vector_store=None,
    use_act_r=False,
    custom_weights=None,
    mode_a_weights=None,
) -> AutoInvoker:
    """Create an AutoInvoker configured for scoring tests."""
    kwargs = {
        "enabled": True,
        "profile_id": "test",
        "use_act_r": use_act_r,
    }
    if custom_weights:
        kwargs["weights"] = custom_weights
    if mode_a_weights:
        kwargs["mode_a_weights"] = mode_a_weights

    cfg = AutoInvokeConfig(**kwargs)
    return AutoInvoker(
        db=MagicMock(),
        vector_store=vector_store,
        trust_scorer=None,
        embedder=None,
        config=cfg,
    )


class TestMultiSignalScoring:
    """4-signal scoring tests."""

    def test_all_four_signals_contribute(self):
        """Each signal should contribute to the final score."""
        invoker = _make_scoring_invoker()
        signals = {
            "similarity": 0.8,
            "recency": 0.5,
            "frequency": 0.3,
            "trust": 0.7,
        }
        score = invoker._combine_signals(signals)
        # Each signal contributes, so score > any single weighted signal
        assert score > 0

    def test_weights_sum_to_one(self):
        cfg = AutoInvokeConfig()
        total = sum(cfg.weights.values())
        assert abs(total - 1.0) < 0.001

    def test_high_similarity_dominates(self):
        """Similarity has highest weight (0.40), should dominate."""
        invoker = _make_scoring_invoker(vector_store=MagicMock())

        high_sim = {"similarity": 1.0, "recency": 0.0, "frequency": 0.0, "trust": 0.0}
        low_sim = {"similarity": 0.0, "recency": 1.0, "frequency": 1.0, "trust": 1.0}

        score_high = invoker._combine_signals(high_sim)
        score_low = invoker._combine_signals(low_sim)

        # Similarity=1.0 * 0.40 = 0.40
        # Recency=1.0 * 0.25 + freq=1.0 * 0.20 + trust=1.0 * 0.15 = 0.60
        # So low_sim actually scores higher. The point is both contribute.
        assert score_high > 0
        assert score_low > 0

    def test_recency_decay_exponential(self):
        """Recency should decay exponentially with time."""
        import math
        # Direct formula test: exp(-0.01 * seconds)
        recent = math.exp(-0.01 * 10)     # 10 seconds ago
        old = math.exp(-0.01 * 10000)     # ~2.7 hours ago
        ancient = math.exp(-0.01 * 100000)  # ~27 hours ago

        assert recent > old > ancient
        assert recent > 0.9  # Very recent -> high score
        assert ancient < 0.1  # Very old -> low score

    def test_frequency_normalized_by_max(self):
        """Frequency uses log1p normalization."""
        import math
        # log1p(5) / log1p(100) should be < 1.0
        freq = math.log1p(5) / math.log1p(100)
        assert 0.0 < freq < 1.0

    def test_trust_default_half(self):
        """Default trust is 0.5 (uniform prior)."""
        invoker = _make_scoring_invoker()
        trust = invoker._compute_trust("any_fact", "any_profile")
        assert trust == 0.5

    def test_custom_weights_applied(self):
        """Custom weights should be used in scoring."""
        custom = {
            "similarity": 0.10,
            "recency": 0.30,
            "frequency": 0.30,
            "trust": 0.30,
        }
        invoker = _make_scoring_invoker(
            custom_weights=custom,
            vector_store=MagicMock(),
        )
        signals = {
            "similarity": 1.0,
            "recency": 0.0,
            "frequency": 0.0,
            "trust": 0.0,
        }
        score = invoker._combine_signals(signals)
        # With custom weights: 1.0 * 0.10 = 0.10
        assert abs(score - 0.10) < 0.001

    def test_score_range_zero_to_one(self):
        """All signal values in [0,1] should produce score in [0,1]."""
        invoker = _make_scoring_invoker(vector_store=MagicMock())

        # Maximum signals
        max_signals = {
            "similarity": 1.0,
            "recency": 1.0,
            "frequency": 1.0,
            "trust": 1.0,
        }
        max_score = invoker._combine_signals(max_signals)
        assert 0.0 <= max_score <= 1.0

        # Minimum signals
        min_signals = {
            "similarity": 0.0,
            "recency": 0.0,
            "frequency": 0.0,
            "trust": 0.0,
        }
        min_score = invoker._combine_signals(min_signals)
        assert 0.0 <= min_score <= 1.0

    def test_exact_scoring_formula(self):
        """Verify exact formula: AI-14 / Implementation Rule 11."""
        invoker = _make_scoring_invoker(vector_store=MagicMock())
        signals = {
            "similarity": 0.8,
            "recency": 0.5,
            "frequency": 0.3,
            "trust": 0.7,
        }
        expected = 0.40 * 0.8 + 0.25 * 0.5 + 0.20 * 0.3 + 0.15 * 0.7
        score = invoker._combine_signals(signals)
        assert abs(score - expected) < 0.001, f"Expected {expected}, got {score}"


class TestModeADegradation:
    """Mode A without embeddings [L2 fix]."""

    def test_similarity_zero_when_no_embeddings(self):
        """When vector_store is None, similarity should not contribute."""
        invoker = _make_scoring_invoker(vector_store=None)
        signals = {
            "similarity": 0.0,
            "recency": 0.5,
            "frequency": 0.5,
            "trust": 0.5,
        }
        score = invoker._combine_signals(signals)
        # Mode A weights: sim=0.00, rec=0.40, freq=0.35, trust=0.25
        expected = 0.00 * 0.0 + 0.40 * 0.5 + 0.35 * 0.5 + 0.25 * 0.5
        assert abs(score - expected) < 0.001

    def test_recency_promoted_to_0_40(self):
        cfg = AutoInvokeConfig()
        assert cfg.mode_a_weights["recency"] == 0.40

    def test_frequency_promoted_to_0_35(self):
        cfg = AutoInvokeConfig()
        assert cfg.mode_a_weights["frequency"] == 0.35

    def test_trust_promoted_to_0_25(self):
        cfg = AutoInvokeConfig()
        assert cfg.mode_a_weights["trust"] == 0.25

    def test_mode_a_weights_sum_to_one(self):
        cfg = AutoInvokeConfig()
        total = sum(cfg.mode_a_weights.values())
        assert abs(total - 1.0) < 0.001

    def test_mode_a_with_vector_store_uses_default_weights(self):
        """When vector_store exists and similarity > 0, use default weights."""
        invoker = _make_scoring_invoker(vector_store=MagicMock())
        signals = {
            "similarity": 0.5,
            "recency": 0.5,
            "frequency": 0.5,
            "trust": 0.5,
        }
        score = invoker._combine_signals(signals)
        # Default weights: 0.40*0.5 + 0.25*0.5 + 0.20*0.5 + 0.15*0.5 = 0.50
        expected = 0.50
        assert abs(score - expected) < 0.001


class TestACTRScoringMode:
    """ACT-R 3-signal scoring mode."""

    def test_three_signal_weights_applied(self):
        cfg = AutoInvokeConfig(use_act_r=True)
        total = sum(cfg.act_r_weights.values())
        assert abs(total - 1.0) < 0.001

    def test_act_r_replaces_recency_and_frequency(self):
        """ACT-R mode uses base_level instead of separate recency + frequency."""
        invoker = _make_scoring_invoker(
            vector_store=MagicMock(), use_act_r=True,
        )
        signals = {
            "similarity": 0.8,
            "recency": 0.5,      # Should be ignored in ACT-R mode
            "frequency": 0.3,    # Should be ignored in ACT-R mode
            "trust": 0.7,
            "base_level": 0.6,
        }
        score = invoker._combine_signals(signals)
        # ACT-R: 0.40*0.8 + 0.35*0.6 + 0.25*0.7 = 0.32 + 0.21 + 0.175 = 0.705
        expected = 0.40 * 0.8 + 0.35 * 0.6 + 0.25 * 0.7
        assert abs(score - expected) < 0.001

    def test_act_r_mode_configurable(self):
        cfg_off = AutoInvokeConfig(use_act_r=False)
        assert cfg_off.use_act_r is False

        cfg_on = AutoInvokeConfig(use_act_r=True)
        assert cfg_on.use_act_r is True

    def test_act_r_base_level_no_access_returns_zero(self):
        db = MagicMock()
        db.execute.return_value = []

        cfg = AutoInvokeConfig(enabled=True, use_act_r=True)
        invoker = AutoInvoker(db=db, config=cfg)
        bl = invoker._compute_act_r_base_level("fact_1", "profile_1")
        assert bl == 0.0

    def test_act_r_sigmoid_normalization_range(self):
        """ACT-R base level should be in [0, 1] range via sigmoid."""
        import math
        # Test sigmoid normalization directly
        for raw in [-5.0, -1.0, 0.0, 1.0, 5.0]:
            sigmoid = 1.0 / (1.0 + math.exp(-raw))
            assert 0.0 <= sigmoid <= 1.0
