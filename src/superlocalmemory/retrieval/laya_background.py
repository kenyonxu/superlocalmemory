# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Cooperative background transport without disrupting foreground inference."""
import logging
import time

from superlocalmemory.core import recall_gate

logger = logging.getLogger(__name__)


def read_line(reader, deadline):
    if not recall_gate.is_background_work():
        return reader.readline(deadline)
    while not recall_gate.background_preempt_requested():
        line = reader.readline(min(deadline, time.monotonic() + 0.05))
        if line is not None or time.monotonic() >= deadline:
            return line
    return None


def exchange(judge, req, timeout_s):
    for _ in range(3):
        if recall_gate.background_preempt_requested() or judge._shutdown.is_set():
            return None
        with recall_gate.idle_wait_deadline(time.monotonic() + 0.05):
            recall_gate.wait_for_foreground_idle()
        if recall_gate.background_preempt_requested():
            return None
        if recall_gate.in_flight() == 0 and judge._lock.acquire(blocking=False):
            break
        time.sleep(0.005)
    else:
        return None
    proc = judge._proc
    try:
        if proc is None or proc.poll() is not None or not judge._ready:
            return None
        deadline = time.monotonic() + timeout_s
        if not judge._drain_stale(proc, deadline) or recall_gate.background_preempt_requested():
            return None
        return judge._exchange(proc, req, deadline)
    except (BrokenPipeError, EOFError, OSError, ValueError) as exc:
        logger.warning("Laya background transport failed: %s", type(exc).__name__)
        judge._worker_lost()
        return None
    finally:
        judge._after_use(proc)
        judge._lock.release()
