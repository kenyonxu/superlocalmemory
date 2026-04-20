# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — Stage 8 H-14

"""Micro-benchmark: Python import cold-start for hook entry points.

Hooks run in the Claude Code hot path on every user prompt and tool
result. A slow Python import (> 500 ms) is a visible UX regression.

We measure the wall-clock of ``python -c 'from
superlocalmemory.hooks.<module> import main'`` in a *fresh* subprocess
— so no bytecode cache is re-used across runs in the warmest sense
(the OS page cache still helps, which is fine: that is what the user
gets in production too).

The assertion is on the p95 of 5 runs, not the mean, because the
p99 CI runner tail is what hurts humans. Budget defaults to 500 ms
and can be relaxed on slow CI via ``SLM_HOOK_COLDSTART_BUDGET_MS``.

Skipped on Windows — subprocess import paths differ enough there
that the number would not be comparable, and the Claude Code hooks
are POSIX-first.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time

import pytest


# Hook modules to measure. Verified via ``ls src/superlocalmemory/hooks/``
# on 2026-04-20 — the rehash entry point is ``user_prompt_rehash_hook``,
# not ``user_prompt_rehash``.
_HOOK_MODULES = (
    "superlocalmemory.hooks.post_tool_outcome_hook",
    "superlocalmemory.hooks.user_prompt_rehash_hook",
)

_DEFAULT_BUDGET_MS = 500.0
_RUNS = 5


def _budget_ms() -> float:
    raw = os.environ.get("SLM_HOOK_COLDSTART_BUDGET_MS")
    if not raw:
        return _DEFAULT_BUDGET_MS
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_BUDGET_MS


def _measure_cold_start(module_name: str) -> list[float]:
    """Return N wall-clock durations (ms) for ``from <module> import main``."""
    durations: list[float] = []
    cmd = [sys.executable, "-c", f"from {module_name} import main"]
    for _ in range(_RUNS):
        t0 = time.perf_counter()
        result = subprocess.run(cmd, capture_output=True, check=False)
        t1 = time.perf_counter()
        assert result.returncode == 0, (
            f"Import of {module_name} failed: "
            f"stderr={result.stderr.decode('utf-8', 'replace')}"
        )
        durations.append((t1 - t0) * 1000.0)
    return durations


def _p95(values: list[float]) -> float:
    """Nearest-rank p95 — deterministic, no numpy dependency."""
    if not values:
        return 0.0
    ordered = sorted(values)
    # For N=5, nearest-rank p95 is index 4 (the max). This is intentional —
    # p95 of 5 samples is the worst observation, which is the honest
    # regression signal on a tiny sample.
    rank = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))
    return ordered[rank]


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Hook cold-start is POSIX-first; Windows subprocess timing not comparable",
)
@pytest.mark.parametrize("module_name", _HOOK_MODULES)
def test_hook_cold_start_under_budget(module_name: str) -> None:
    budget = _budget_ms()
    durations = _measure_cold_start(module_name)
    p95 = _p95(durations)
    mean = sum(durations) / len(durations)

    # Surface the numbers even on pass — useful for trend-tracking in CI logs.
    print(
        f"[hook-coldstart] {module_name}: "
        f"mean={mean:.1f}ms p95={p95:.1f}ms "
        f"runs={durations} budget={budget:.0f}ms"
    )

    assert p95 < budget, (
        f"{module_name} cold-start p95={p95:.1f}ms exceeds budget={budget:.0f}ms. "
        f"All runs: {durations}"
    )
