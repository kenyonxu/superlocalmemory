# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Wall-clock jump detector.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

from typing import Literal, Optional

Event = Literal["forward", "backward"]


class ClockJumpDetector:
    """Detects NTP-style jumps by comparing wall-clock and monotonic deltas."""

    def __init__(self, threshold_s: float = 1.0) -> None:
        self._threshold = threshold_s
        self._last_wall: Optional[float] = None
        self._last_mono: Optional[float] = None
        self.last_drift_s: float = 0.0
        self.last_event: Optional[Event] = None

    def tick(self, wall: float, monotonic: float) -> Optional[Event]:
        if self._last_wall is None or self._last_mono is None:
            self._last_wall = wall
            self._last_mono = monotonic
            return None
        dw = wall - self._last_wall
        dm = monotonic - self._last_mono
        drift = dw - dm
        self._last_wall = wall
        self._last_mono = monotonic
        self.last_drift_s = drift
        if abs(drift) < self._threshold:
            self.last_event = None
            return None
        self.last_event = "forward" if drift > 0 else "backward"
        return self.last_event

    def drift_magnitude_s(self) -> float:
        return abs(self.last_drift_s)
