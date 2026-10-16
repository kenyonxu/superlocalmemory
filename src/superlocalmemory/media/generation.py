"""A counter that moves whenever pictures or who may see them change.

Picture search keeps what it worked out about visible memories for a short while. Every
picture write and every scope change bumps this number, and a kept answer from an older
number is not used.
"""

from __future__ import annotations

import itertools
import threading

_lock = threading.Lock()
_counter = itertools.count(1)
_value = 0


def bump_generation() -> int:
    global _value
    with _lock:
        _value = next(_counter)
        return _value


def generation() -> int:
    return _value


__all__ = ["bump_generation", "generation"]
