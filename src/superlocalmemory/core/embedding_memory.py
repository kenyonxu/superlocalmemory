# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Footprint-aware embedding admission and pressure recovery hysteresis."""

from __future__ import annotations

import logging
import math
import os
import sys
from time import monotonic
from typing import Any

import psutil

from superlocalmemory.infra import proc_memory

logger = logging.getLogger(__name__)
RESERVE_MB = 400.0
RECOVERY_MARGIN_MB = 512.0
COOLDOWN_S = 60.0


def load_floor_mb() -> float:
    try:
        value = float(os.environ.get("SLM_MIN_AVAILABLE_MEMORY_GB", "2.0"))
        return max(0.0, value * 1024) if math.isfinite(value) else 2048.0
    except ValueError:
        return 2048.0


def available_mb() -> float | None:
    try:
        vm = psutil.virtual_memory()
        available = vm.free + vm.inactive if sys.platform == "darwin" else vm.available
        return float(available) / 1024**2
    except (OSError, AttributeError, ValueError):
        logger.debug("embedding memory headroom unavailable", exc_info=True)
        return None


def footprint_mb(pid: Any) -> float:
    return proc_memory.process_memory_mb(pid) if isinstance(pid, int) and pid > 0 else 0.0


def enough_memory(worker_pid: int | None = None) -> bool:
    """Resident models already paid their load footprint; keep a reserve for the OS."""
    available, floor = available_mb(), load_floor_mb()
    if available is None or floor <= 0:
        return True
    footprint = footprint_mb(worker_pid)
    required = max(min(RESERVE_MB, floor), floor - footprint) if footprint else floor
    return available >= required


class PressurePause:
    """A pressure stop cannot be undone by recall-health resetting availability."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.retry_at = 0.0
        self.footprint = 0.0
        self.available: float | None = None

    def block(self, worker_pid: int | None = None) -> None:
        measured = footprint_mb(worker_pid)
        if measured > 0:  # the latest reading; a worker already gone keeps the last one
            self.footprint = measured
        self.available = available_mb()
        headroom = "unknown" if self.available is None else f"{self.available:.0f} MB"
        self.reason = (f"Embedding worker paused for memory pressure ({headroom} available, "
                       f"{self.footprint:.0f} MB worker footprint). "
                       "Waiting for recovered headroom.")
        self.retry_at = monotonic() + COOLDOWN_S
        logger.warning("%s", self.reason)

    def may_start(self) -> bool:
        if self.reason is None:
            return True
        if monotonic() < self.retry_at:
            return False
        self.available = available_mb()
        if self.available is not None and self.available < self.recovery_mb:
            return False  # an unreadable gauge allows the start, as the load check does
        self.reason = None
        return True

    @property
    def recovery_mb(self) -> float:
        return max(load_floor_mb(), self.footprint + RESERVE_MB) + RECOVERY_MARGIN_MB

    def _message(self, retry_in: float) -> str:
        free = "unknown" if self.available is None else f"{self.available:.0f} MB"
        when = (f"next check in {math.ceil(retry_in)} s" if retry_in > 0
                else "it checks again on the next request")
        return (f"Embedding worker paused for memory pressure: {free} free now, "
                f"{self.recovery_mb:.0f} MB needed to resume; {when}.")

    def snapshot(self) -> dict[str, Any]:
        paused = self.reason is not None
        if paused:
            self.available = available_mb()  # a fresh reading, not the one from the pause
        retry_in = max(0.0, self.retry_at - monotonic()) if paused else 0.0
        return {"state": "memory_pressure" if paused else "ready",
                "reason": self._message(retry_in) if paused else None,
                "footprint_mb": round(self.footprint, 1), "available_mb": self.available,
                "recovery_mb": round(self.recovery_mb, 1), "retry_in_s": round(retry_in, 1)}


def guard_for(service: Any) -> PressurePause:
    return service._pressure_pause


def runtime_status(app_state: Any) -> dict | None:
    """Read the daemon's existing embedder without starting a model or taking its request lock."""
    engine = getattr(app_state, "engine", None)
    embedder = getattr(engine, "_embedder", None)
    if embedder is None:
        embedder = getattr(getattr(engine, "_retrieval_engine", None), "_embedder", None)
    snapshot = getattr(embedder, "memory_status", None)
    return snapshot() if callable(snapshot) else None
