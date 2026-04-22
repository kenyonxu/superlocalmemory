# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for core.clock_monitor."""

from __future__ import annotations

import time

import pytest


def _imports():
    from superlocalmemory.core import clock_monitor as cm
    return cm


def test_detector_no_drift() -> None:
    cm = _imports()
    d = cm.ClockJumpDetector(threshold_s=1.0)
    d.tick(wall=100.0, monotonic=10.0)
    d.tick(wall=101.0, monotonic=11.0)
    assert d.last_drift_s == 0.0
    assert d.last_event is None


def test_detector_forward_jump() -> None:
    cm = _imports()
    d = cm.ClockJumpDetector(threshold_s=1.0)
    d.tick(wall=100.0, monotonic=10.0)
    # 5s wall advance, 1s monotonic advance => forward jump of 4s
    d.tick(wall=105.0, monotonic=11.0)
    assert d.last_event == "forward"
    assert d.last_drift_s == pytest.approx(4.0, abs=0.01)


def test_detector_backward_jump() -> None:
    cm = _imports()
    d = cm.ClockJumpDetector(threshold_s=1.0)
    d.tick(wall=100.0, monotonic=10.0)
    # wall rolled back 3s while monotonic advanced 1s
    d.tick(wall=98.0, monotonic=11.0)
    assert d.last_event == "backward"
    assert d.last_drift_s == pytest.approx(-3.0, abs=0.01)


def test_detector_drift_below_threshold_not_event() -> None:
    cm = _imports()
    d = cm.ClockJumpDetector(threshold_s=2.0)
    d.tick(wall=100.0, monotonic=10.0)
    d.tick(wall=101.5, monotonic=11.0)  # 0.5s drift
    assert d.last_event is None


def test_detector_exposes_drift_magnitude_for_policy() -> None:
    cm = _imports()
    d = cm.ClockJumpDetector(threshold_s=1.0)
    d.tick(wall=100.0, monotonic=10.0)
    d.tick(wall=107.0, monotonic=11.0)
    assert d.drift_magnitude_s() == pytest.approx(6.0, abs=0.01)
