# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Own manual component repair workers through shutdown and test teardown."""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_WORKERS: set[threading.Thread] = set()


def start_manual_heal(state, repair) -> bool:
    """One repair per application; retain its handle before it can run."""
    with _LOCK:
        if getattr(state, "manual_heal_closed", False):
            return False
        previous = getattr(state, "manual_heal_thread", None)
        if previous is not None and previous.is_alive():
            return True
        _WORKERS.difference_update(w for w in list(_WORKERS) if not w.is_alive())
        worker = threading.Thread(target=repair, daemon=True, name="manual-heal")
        state.manual_heal_thread = worker
        _WORKERS.add(worker)
        try:
            worker.start()
        except RuntimeError:
            _WORKERS.discard(worker)
            state.manual_heal_thread = None
            return False
        return True


def join_manual_heals(state=None, timeout_s: float | None = 5.0) -> bool:
    """Join owned workers before dependencies or subprocess patches disappear.

    Shutdown closes the supplied application's admission gate first. Tests
    without a lifespan join all workers started in this interpreter instead.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    with _LOCK:
        if state is not None:
            state.manual_heal_closed = True
            worker = getattr(state, "manual_heal_thread", None)
            workers = [worker] if worker is not None else []
        else:
            workers = list(_WORKERS)
    for worker in workers:
        worker.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
    with _LOCK:
        _WORKERS.difference_update(w for w in workers if not w.is_alive())
    return all(not w.is_alive() for w in workers)
