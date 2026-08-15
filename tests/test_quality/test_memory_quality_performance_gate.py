"""Release-runner contracts for the redacted SLM 4.0.5 performance gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RUNNER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "quality"
    / "run_memory_quality_performance_gate.py"
)
_SPEC = importlib.util.spec_from_file_location("slm_performance_gate", _RUNNER)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_gate_exercises_declared_counts_without_live_data(tmp_path: Path) -> None:
    """A short test run proves the exact runner shape; release runs use 60s."""
    report = _MODULE.run_gate(workdir=tmp_path, mixed_duration_seconds=0.25)

    assert report["contract"] == "slm.dev/memory-quality-performance/v1"
    assert report["samples"] == {
        "warmup": 5,
        "warm_recalls": 50,
        "remember_acknowledgements": 50,
        "mixed_duration_seconds": 0.25,
        "mixed_readers": 10,
        "mixed_writers": 2,
    }
    assert report["metrics"]["mixed"]["recalls"] > 0
    assert report["metrics"]["mixed"]["remembers"] > 0
    assert report["metrics"]["mixed"]["timeouts"] == 0
    assert report["metrics"]["mixed"]["locks"] == 0
    assert report["metrics"]["mixed"]["errors"] == 0
