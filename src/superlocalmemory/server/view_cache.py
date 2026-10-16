# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com
"""Dashboard summaries computed once and shared for a few seconds.

Every open dashboard polls the same counts (memory health, totals). Each read
scans large tables, so many dashboards and agents together kept the store busy
answering one question over and over. A summary is now computed once per key
and shared for ``ttl_s`` seconds; callers that arrive while it is being
computed wait for that one computation instead of starting their own. A write
in this process does not drop it (agents write constantly, and a count a few
seconds old is the honest cost of not scanning on every poll); an action that
changes what the summary shows calls :func:`drop`.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_entries: dict[tuple, tuple[float, Any]] = {}
_building: dict[tuple, threading.Event] = {}
MAX_ENTRIES = 256


def cached(name: str, key: tuple, ttl_s: float, build: Callable[[], Any]) -> Any:
    """``build()`` at most once per ``ttl_s`` per key; repeats get a copy."""
    full = (name, *key)
    while True:
        with _lock:
            hit = _entries.get(full)
            if hit is not None and time.monotonic() - hit[0] < ttl_s:
                return copy.deepcopy(hit[1])
            waiting = _building.get(full)
            if waiting is None:
                done = threading.Event()
                _building[full] = done
                break
        if not waiting.wait(timeout=30):
            return build()  # the builder is stuck: answer this caller directly
    try:
        value = build()
        with _lock:
            if len(_entries) >= MAX_ENTRIES:
                _entries.pop(next(iter(_entries)))
            _entries[full] = (time.monotonic(), copy.deepcopy(value))
        return value
    finally:
        with _lock:
            _building.pop(full, None)
        done.set()


def drop(name: str | None = None) -> None:
    """Forget one summary (by name) or all of them."""
    with _lock:
        for key in [k for k in _entries if name is None or k[0] == name]:
            _entries.pop(key, None)


__all__ = ["cached", "drop"]
