from __future__ import annotations

import sys
from pathlib import Path

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmark"
sys.path.insert(0, str(BENCHMARK))

import exp12_learning_loop_ablation as exp12  # noqa: E402


def test_each_round_in_each_arm_counts_as_a_trial():
    result = exp12.run(n_trials=2)

    assert result.trials == 6
    assert result.held == 6
    assert result.passed is True
