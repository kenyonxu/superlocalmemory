# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
# Part of SuperLocalMemory V3
"""Enrichment pool lifecycle: shutdown and bounded submission.

Two defects:
  1. The enrichment pool (ThreadPoolExecutor, 2 non-daemon threads) is created
     lazily on the first remember request and is never shut down in the lifespan
     teardown.  Python's atexit calls shutdown(wait=True) on process exit, so a
     worker blocked inside a slow embed pushes the daemon past its
     graceful-shutdown budget and the service manager SIGKILLs it.

  2. When both workers are occupied, additional callers are silently queued by
     the unbounded ThreadPoolExecutor queue rather than being rejected.  A burst
     of writes lands as a thundering herd after the callers have already received
     a wording-only response.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from unittest.mock import patch

import pytest

import superlocalmemory.server.unified_daemon as ud


@pytest.fixture(autouse=True)
def _fresh_pool_state():
    """Give every test the state a process start would give it.

    The pool and its closed flag are module-level, and staying closed across a
    shutdown is the point of the flag — so without this a test that shuts the
    pool down leaves every later test unable to create one, and the failure lands
    on whichever test happens to run next rather than on the one responsible.
    """
    ud._open_enrichment_pool()
    yield
    ud._shutdown_enrichment_pool_if_created()
    ud._open_enrichment_pool()


# ---------------------------------------------------------------------------
# The pool's threads must be gone after teardown
# ---------------------------------------------------------------------------

class TestEnrichmentPoolReleasedAtTeardown:
    """The enrichment pool must be shut down during lifespan teardown.

    This test creates the pool (as the lifespan startup would), submits a task
    to guarantee at least one thread is alive, then calls the shutdown helper
    that should be invoked from the teardown.  The test is the specification:
    if the helper does not exist, or does not stop the threads, it fails.
    """

    def _make_pool_and_start_thread(self):
        """Create the pool and start a thread in it.  Returns (pool, barrier)."""
        import superlocalmemory.server.unified_daemon as ud

        # Reset any existing pool so we start from a known state.
        with ud._enrichment_pool_lock:
            if ud._enrichment_pool is not None:
                ud._enrichment_pool.shutdown(wait=False, cancel_futures=True)
                ud._enrichment_pool = None

        pool = ud._enrichment_executor()

        # Keep the thread alive until the barrier is released.
        barrier = threading.Barrier(2, timeout=5.0)
        pool.submit(barrier.wait)
        barrier.wait()  # our side — thread is now alive inside barrier.wait
        time.sleep(0.05)
        return pool

    def test_slm_enrich_thread_is_running_after_pool_creation(self) -> None:
        """Baseline: the pool does create threads named slm-enrich-*.

        If this assertion fails the test environment itself is the problem.
        """
        pool = self._make_pool_and_start_thread()
        alive = [t for t in threading.enumerate() if t.name.startswith("slm-enrich")]
        try:
            assert alive, (
                "No slm-enrich thread found after pool creation. "
                f"All threads: {[t.name for t in threading.enumerate()]}"
            )
        finally:
            # Best-effort cleanup — do not leave a zombie thread between tests.
            pool.shutdown(wait=False, cancel_futures=True)

    def test_pool_shutdown_is_callable_from_teardown(self) -> None:
        """The lifespan teardown must have a reachable path that shuts the pool.

        Specifically, a helper named _shutdown_enrichment_pool_if_created must
        exist at module level and must be callable without arguments so the
        teardown can invoke it without knowing the pool's internal state.

        RED: this test fails with AttributeError because the function does not
        exist yet.  GREEN: after the function is added.
        """
        import superlocalmemory.server.unified_daemon as ud

        # The function must exist.
        fn = getattr(ud, "_shutdown_enrichment_pool_if_created", None)
        assert fn is not None and callable(fn), (
            "_shutdown_enrichment_pool_if_created is missing from unified_daemon. "
            "The lifespan teardown needs this function to shut down the enrichment "
            "pool before engine.close() is called."
        )

    def test_threads_stop_after_shutdown_helper(self) -> None:
        """After calling the helper, slm-enrich threads must be gone.

        RED: test will fail at the hasattr guard above (function missing).
        Once the function is added (GREEN), the thread must actually stop.
        """
        import superlocalmemory.server.unified_daemon as ud

        self._make_pool_and_start_thread()

        alive_before = [t for t in threading.enumerate() if t.name.startswith("slm-enrich")]
        assert alive_before, "Setup failed: no slm-enrich thread running before shutdown"

        # This is the call the lifespan teardown will make.
        ud._shutdown_enrichment_pool_if_created()

        # Give threads up to 1 s to stop.  shutdown(wait=False, cancel_futures=True)
        # should cause them to exit promptly.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            still_alive = [
                t for t in threading.enumerate() if t.name.startswith("slm-enrich")
            ]
            if not still_alive:
                break
            time.sleep(0.05)

        still_alive = [t for t in threading.enumerate() if t.name.startswith("slm-enrich")]
        assert not still_alive, (
            f"slm-enrich thread(s) still running after pool shutdown: "
            f"{[t.name for t in still_alive]}"
        )

    def test_shutdown_is_safe_when_pool_was_never_created(self) -> None:
        """The helper must be safe to call even if the pool was never initialised.

        Lifespan teardown always runs the finally block; the pool might never have
        been created if no remember request arrived before SIGTERM.

        RED: function missing → AttributeError.
        GREEN: function exists and handles None pool gracefully.
        """
        import superlocalmemory.server.unified_daemon as ud

        # Force the pool to None.
        with ud._enrichment_pool_lock:
            original = ud._enrichment_pool
            ud._enrichment_pool = None

        try:
            # Must not raise.
            ud._shutdown_enrichment_pool_if_created()
        finally:
            # Restore original state.
            with ud._enrichment_pool_lock:
                ud._enrichment_pool = original

    def test_second_shutdown_is_idempotent(self) -> None:
        """Calling the helper twice must not raise.

        The atexit handler may also call shutdown; two calls must be safe.

        RED: function missing.  GREEN: double-call is harmless.
        """
        import superlocalmemory.server.unified_daemon as ud

        # Create and shut down.
        ud._enrichment_executor()
        ud._shutdown_enrichment_pool_if_created()
        # Second call — must be a no-op.
        ud._shutdown_enrichment_pool_if_created()


# ---------------------------------------------------------------------------
# Bounded submission: over-budget callers must not silently queue
# ---------------------------------------------------------------------------

# Bounded submission is covered by test_enrichment_submission_is_bounded.py,
# which drives the real write route. A test that only checks the semaphore
# object exists and has the right capacity is satisfied by a semaphore nothing
# consults — which is exactly the state this code was in, so those assertions
# were removed rather than kept alongside the ones that can fail.
