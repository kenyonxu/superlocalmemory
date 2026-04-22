# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tick-based watchdog that fires once when a cooperative loop goes silent.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class LoopWatchdog:
    def __init__(
        self,
        stale_threshold_s: float,
        on_stale: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._threshold = stale_threshold_s
        self._on_stale = on_stale
        self._last_tick = time.monotonic()
        self._fired = False
        self._lock = threading.Lock()

    def tick(self) -> None:
        with self._lock:
            self._last_tick = time.monotonic()
            self._fired = False

    def age_s(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_tick

    def is_stale(self) -> bool:
        return self.age_s() >= self._threshold

    def check(self) -> bool:
        """Fire callback once if stale; return True if just fired."""
        with self._lock:
            age = time.monotonic() - self._last_tick
            if age < self._threshold or self._fired:
                return False
            self._fired = True
            cb = self._on_stale
        if cb is not None:
            cb(age)
        return True

    def run_forever(self, stop: threading.Event, interval_s: float = 1.0) -> None:
        while not stop.is_set():
            self.check()
            stop.wait(timeout=interval_s)
