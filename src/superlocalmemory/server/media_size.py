"""Measure the installed media environment without following links.

The walk touches every file of a Python environment (tens of thousands), about
a second on a real install, so request handlers use :func:`cached_bytes`: it
answers from memory and refreshes the reading on a background thread.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

#: How long a measured size is served before a new reading is taken.
CACHE_SECONDS = 30.0

_lock = threading.Lock()
_readings: dict[str, tuple[int, float]] = {}
_pending: dict[str, threading.Thread] = {}


def installed_bytes(root: Path) -> int:
    total = 0
    seen = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()]
        for name in files:
            try:
                entry = (Path(folder) / name).lstat()
            except OSError:
                continue
            identity = (entry.st_dev, entry.st_ino)
            if stat.S_ISREG(entry.st_mode) and identity not in seen:
                total += entry.st_size
                seen.add(identity)
    return total


def measure_now(root: Path) -> int:
    """Walk ``root`` now (callers that just changed it) and remember the result."""
    size = installed_bytes(root)
    with _lock:
        _readings[str(root)] = (size, time.monotonic())
    return size


def _refresh(root: Path) -> None:
    try:
        measure_now(root)
    except OSError:
        pass  # keep serving the last reading; the next request tries again
    finally:
        with _lock:
            _pending.pop(str(root), None)


def cached_bytes(root: Path) -> int:
    """The last measured size, instantly; 0 until the first reading arrives."""
    if not Path(root).is_dir():
        return 0
    key = str(root)
    with _lock:
        size, taken = _readings.get(key, (0, None))
        if taken is not None and time.monotonic() - taken < CACHE_SECONDS:
            return size
        if key not in _pending:
            worker = threading.Thread(
                target=_refresh, args=(root,), name="media-size", daemon=True)
            _pending[key] = worker
            worker.start()
        return size


def wait_idle(timeout: float) -> bool:
    """Wait for background readings to finish (tests, shutdown)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            workers = list(_pending.values())
        if not workers:
            return True
        workers[0].join(0.05)
    return False


def forget_all() -> None:
    with _lock:
        _readings.clear()
