# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com
"""A counter that moves whenever this process writes to the memory store.

Kept recall answers carry the number they were computed under; once it moves,
they are not served again. The store manager bumps it on every write statement
and again after each commit, so a recall that started before a write never
answers a question asked after it. Writes from other processes are not seen
here; the recall cache's short lifetime bounds those.
"""

from __future__ import annotations

import itertools
import threading

_lock = threading.Lock()
_counter = itertools.count(1)
_value = 0


def bump() -> int:
    global _value
    with _lock:
        _value = next(_counter)
        return _value


def current() -> int:
    return _value


__all__ = ["bump", "current"]
