# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Short-lived answers for summary endpoints the dashboard asks for over and over.

A summary that counts every row, or walks a large folder, is cheap once and a
burden when fifteen panes ask at the same moment while the daemon is busy with a
background job. ``cached`` computes it once per ``ttl`` seconds; callers that
arrive while it is being computed wait for that one computation instead of
starting their own. Nothing here is stored on disk.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

_values: dict[Any, tuple[float, Any]] = {}
_locks: dict[Any, threading.Lock] = {}
_registry = threading.Lock()


def _lock_for(key: Any) -> threading.Lock:
    with _registry:
        return _locks.setdefault(key, threading.Lock())


def cached(key: Any, ttl: float, compute: Callable[[], Any],
           keep: Callable[[Any], bool] = lambda _value: True) -> Any:
    """The value for ``key``, at most ``ttl`` seconds old; a copy, so callers may edit it.

    ``keep`` says whether a freshly computed value may be remembered (an error answer
    should not be).
    """
    now = time.monotonic()
    hit = _values.get(key)
    if hit is not None and now - hit[0] < ttl:
        return copy.deepcopy(hit[1])
    with _lock_for(key):
        hit = _values.get(key)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return copy.deepcopy(hit[1])
        value = compute()
        if keep(value):
            _values[key] = (time.monotonic(), value)
        return copy.deepcopy(value)


def forget(key: Any = None) -> None:
    """Drop one answer (after the thing it summarises changed) or all of them."""
    with _registry:
        if key is None:
            _values.clear()
        else:
            _values.pop(key, None)
