# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com
#
# Concurrency soak test: WAL close-path deadlock guard — Invariant I2 for SLM 4.0.6.
#
# Invariant I2: "No DB deadlocks — concurrency soak; writers x readers x connection churn."
#
# Root cause guarded against (incident 2026-08-13, fixed in PR #118 + 4.0.6):
#
#   Closing a WAL-mode sqlite3 connection triggers sqlite3WalClose(), which
#   checkpoints the WAL.  That checkpoint can wait indefinitely on WAL reader
#   marks pinned by another open connection — while holding SQLite's
#   process-global VFS mutex.  PRAGMA busy_timeout does NOT apply to the close
#   path.  Consequently, every concurrent sqlite3.connect() in the process
#   convoys behind the blocked close(), stalling ALL readers and writers.
#
#   Fix: every DatabaseManager._connect() connection sets
#   SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE=1, so close() is always checkpoint-free.
#   Routine checkpointing continues via PRAGMA wal_autocheckpoint=400.
#
# Soak design:
#
#   N_WRITERS writer threads   — tight store_memory()+store_fact() loop; each op
#                                opens a connection, writes, commits, closes — the
#                                close is what (without the fix) triggers the convoy.
#
#   N_READERS reader threads   — concurrent get_all_facts()+get_fact_count() loop;
#                                readers do NOT go through _lock so they call
#                                _connect() independently and convoy on the VFS mutex
#                                when the fix is absent.
#
#   N_CHURN pure-churn threads — _connect() → SELECT 1 → close() without writing;
#                                extra close events to maximise checkpoint-on-close
#                                pressure and VFS-mutex contention.
#
#   1 long-lived WAL reader    — direct sqlite3.connect() (bypasses
#                                DatabaseManager, so NO_CKPT_ON_CLOSE is absent) +
#                                BEGIN + SELECT held for READER_HOLD_S without
#                                COMMIT/ROLLBACK.  This pins the WAL reader mark.
#                                The checkpoint-on-close from writer closes blocks
#                                waiting on this mark.  This is the exact production
#                                shape that caused the incident.
#
#   1 watchdog thread          — detects complete stall: fires if ALL tracked
#                                workers make zero combined progress for
#                                STALL_TIMEOUT_S after warmup.
#
# Assertions:
#   1. Watchdog did not fire (no near-total hang).
#   2. Every writer completed >= MIN_WRITER_OPS (forward-progress / throughput floor).
#   3. No unhandled exceptions from any worker thread.
#
# Discriminating power (verified by reverting the fix — see test report):
#
#   Without NO_CKPT_ON_CLOSE each writer close() blocks for ~READER_HOLD_S while
#   the long-lived reader mark is pinned.  Because the RLock serialises writers,
#   the single active writer holds the lock for READER_HOLD_S per op.  Each
#   store_memory()+store_fact() involves 3 open/close cycles (memory INSERT,
#   fact dedup SELECT, fact transaction INSERT), so one "writer op" takes
#   ~3 × READER_HOLD_S seconds.  In SOAK_SECONDS total, each writer can complete
#   at most SOAK_SECONDS / (3 × READER_HOLD_S) ≈ 1–2 ops → fails MIN_WRITER_OPS.
#   With the fix: each close() is instant; 50–300 ops per writer easily.
#
# See also:
#   tests/test_storage/test_no_ckpt_on_close.py   — unit: close-path property
#   tests/test_storage/test_wal_autocheckpoint.py  — unit: autocheckpoint pragma

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, FactType, MemoryRecord

# ---------------------------------------------------------------------------
# Soak parameters
# ---------------------------------------------------------------------------

SOAK_SECONDS: int = 25
"""Total soak duration in seconds.  Must be <= 60s (CI budget)."""

WARMUP_S: float = 2.0
"""Stall detection ignores the first WARMUP_S seconds (schema + pre-populate)."""

STALL_TIMEOUT_S: float = 7.0
"""Watchdog fires if ALL tracked workers show zero combined progress for this long.

Tuned to be > READER_HOLD_S so legitimate slowdowns don't false-fire, but the
watchdog catches a near-total hang where stop.is_set() was never reached due to
the VFS mutex convoy.
"""

CHECK_INTERVAL_S: float = 0.25
"""Watchdog polling interval."""

N_WRITERS: int = 4
"""Concurrent writer threads."""

N_READERS: int = 3
"""Concurrent reader threads (do NOT go through _lock; expose VFS contention)."""

N_CHURN: int = 2
"""Pure-churn threads: _connect() → SELECT 1 → close(), no writes.

These add extra close-path pressure and VFS mutex contention without using the
single-writer RLock, making the convoy more pronounced when the fix is absent.
"""

READER_HOLD_S: float = 5.0
"""Long-lived reader holds the WAL reader mark for this many seconds per iteration.

Must be > STALL_TIMEOUT_S so that if the stall actually triggers (fix absent),
the watchdog fires before the reader releases its mark.  5 > 4 ✓ (STALL=7, but
the actual "no ops" window when the writer is blocked is the full hold duration,
and we rely on MIN_WRITER_OPS as the primary discriminator; watchdog is a
secondary last-resort detector).
"""

MIN_WRITER_OPS: int = 20
"""Minimum successful ops per writer thread over SOAK_SECONDS.

Primary discriminator between "fix active" and "fix absent":
  - Fix active:  typically 50–300 ops/writer → passes easily.
  - Fix absent:  ~SOAK_SECONDS / (3 × READER_HOLD_S) ≈ 1–2 ops/writer → fails.
"""

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


_PROFILE = "default"
"""Profile used by all soak workers.  Must exist in the profiles table;
create_all_tables() inserts 'default' automatically."""


def _make_memory() -> MemoryRecord:
    return MemoryRecord(profile_id=_PROFILE, content=f"soak-mem-{_uid()}")


def _make_fact(memory_id: str) -> AtomicFact:
    return AtomicFact(
        profile_id=_PROFILE,
        memory_id=memory_id,
        content=f"soak-fact-{_uid()}",
        fact_type=FactType.SEMANTIC,
    )


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------


def _writer_worker(
    db: DatabaseManager,
    stop: threading.Event,
    progress: list[int],
    errors: list[Exception],
) -> None:
    """Tight store_memory() + store_fact() loop — 3 open/write/commit/close cycles per op.

    Each iteration:
      1. store_memory()  → INSERT memories  → open/commit/close  (1 close event)
      2. store_fact()    → SELECT (dedup)   → open/close         (1 close event)
                        → transaction()    → open/commit/close   (1 close event)

    Without NO_CKPT_ON_CLOSE each close that triggers a checkpoint blocks for
    up to READER_HOLD_S while the long-lived reader mark is pinned.  With the
    RLock serialising writes, ALL other writers queue behind the blocked close.
    """
    count = 0
    while not stop.is_set():
        try:
            rec = _make_memory()
            db.store_memory(rec)
            db.store_fact(_make_fact(rec.memory_id))
            count += 1
            progress[0] = count
        except Exception as exc:
            errors.append(exc)
            # Transient SQLITE_BUSY is retried inside _execute_one(); reaching
            # here means retries were exhausted.  Sleep briefly and continue so
            # the soak measures steady-state throughput, not one bad burst.
            time.sleep(0.05)


def _reader_worker(
    db: DatabaseManager,
    stop: threading.Event,
    progress: list[int],
    errors: list[Exception],
) -> None:
    """Read loop — does NOT go through _lock; uses its own _connect() calls.

    Without the fix these sqlite3.connect() calls block on the VFS mutex while
    a writer's close() holds it during its checkpoint attempt.
    """
    count = 0
    while not stop.is_set():
        try:
            db.get_all_facts(_PROFILE, limit=50)
            db.get_fact_count(_PROFILE)
            count += 1
            progress[0] = count
        except Exception as exc:
            errors.append(exc)
            time.sleep(0.05)


def _churn_worker(
    db: DatabaseManager,
    stop: threading.Event,
    errors: list[Exception],
) -> None:
    """Open a connection, read a row, close — no writes, pure close-path pressure.

    Churn threads maximise the number of close events and VFS mutex contests
    without using the single-writer RLock, making the convoy far more visible
    when the fix is absent.  With the fix these close() calls are instant.
    """
    while not stop.is_set():
        conn: sqlite3.Connection | None = None
        try:
            conn = db._connect()
            conn.execute("SELECT 1")
            # close() here is the critical call: without NO_CKPT_ON_CLOSE it
            # attempts a checkpoint that blocks on the long-lived reader mark.
            conn.close()
            conn = None
        except Exception as exc:
            errors.append(exc)
            time.sleep(0.05)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _long_lived_reader_worker(
    db_path: Path,
    stop: threading.Event,
    errors: list[Exception],
) -> None:
    """Pin a WAL reader mark for READER_HOLD_S per iteration.

    This function DELIBERATELY uses a raw sqlite3.connect() (NOT
    DatabaseManager._connect()) so it is unaffected by the NO_CKPT_ON_CLOSE
    flag.  It starts an explicit BEGIN read transaction and holds it open for
    READER_HOLD_S before rolling back.  This pins the WAL reader mark.

    Without the fix, any writer or churn close() that attempts a checkpoint
    blocks waiting on this reader mark — while holding the VFS mutex — causing
    the convoy.  With the fix, close() never checkpoints, so the reader mark
    causes no harm.

    No sleep between iterations: the reader immediately re-acquires after
    rollback so the mark is always pinned during the soak.
    """
    while not stop.is_set():
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=1.0)
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM memories")
            # Hold the open read transaction — this is the WAL reader mark pin.
            deadline = time.monotonic() + READER_HOLD_S
            while time.monotonic() < deadline and not stop.is_set():
                time.sleep(0.05)
            conn.rollback()
        except Exception as exc:
            errors.append(exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


def _watchdog_worker(
    all_progresses: list[list[int]],
    deadlock_event: threading.Event,
    stop: threading.Event,
) -> None:
    """Fire deadlock_event if all tracked workers show zero combined progress.

    CRIT fix applied: the stall detector ignores WARMUP_S at startup so schema
    initialisation and pre-populate latency do not false-fire.  The threshold
    tracks COMBINED progress across all workers so a single fast thread cannot
    mask a stall in the rest of the pack.
    """
    # Warmup: don't start stall detection until workers are running.
    warmup_end = time.monotonic() + WARMUP_S
    while time.monotonic() < warmup_end and not stop.is_set():
        time.sleep(CHECK_INTERVAL_S)

    last_total = sum(p[0] for p in all_progresses)
    last_advance_time = time.monotonic()

    while not stop.is_set():
        time.sleep(CHECK_INTERVAL_S)
        total = sum(p[0] for p in all_progresses)
        if total > last_total:
            last_total = total
            last_advance_time = time.monotonic()
        elif (time.monotonic() - last_advance_time) >= STALL_TIMEOUT_S:
            deadlock_event.set()
            return


# ---------------------------------------------------------------------------
# The soak test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_concurrency_soak_no_deadlock(tmp_path: Path) -> None:
    """Concurrency soak: N writers × M readers × churn × long-lived WAL reader.

    Runtime: ~SOAK_SECONDS (25s) wall-clock.
    Isolation: tmp_path only — never touches ~/.superlocalmemory.

    Run with:
        pytest tests/test_storage/test_concurrency_soak.py -m slow -s -v

    CRIT flaws found and fixed during authoring:
    1. Pinning condition: long-lived reader uses raw sqlite3.connect() (NOT
       DatabaseManager._connect()) so it is NOT protected by NO_CKPT_ON_CLOSE.
       This ensures the reader mark is actually pinnable and can cause a real
       block on checkpoint.  An oversight using db._connect() would set
       NO_CKPT_ON_CLOSE on the "long-lived" connection, making it harmless and
       defeating the soak's ability to detect the regression.
    2. Per-op vs global timeout: the watchdog fires on COMBINED zero progress
       for STALL_TIMEOUT_S.  A single global join timeout would silently pass
       if threads were merely stalled rather than errored, since join() returns
       when the thread eventually unblocks.  The watchdog is active during the
       soak so it fires and breaks the soak loop early.
    3. Trivial-pass on a fast machine: MIN_WRITER_OPS = 20 over 25s requires
       ~0.8 ops/s per writer.  Without the fix, each op takes ~3 × READER_HOLD_S
       = 15s → at most 1–2 ops in 25s, safely below the floor regardless of
       machine speed.  READER_HOLD_S is explicitly tuned to exceed the op time
       floor so fast machines cannot squeak by.
    """
    db_path = tmp_path / "soak.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize(schema)

    # Pre-populate so readers have rows from the very first iteration.
    for _ in range(20):
        rec = _make_memory()
        mgr.store_memory(rec)
        mgr.store_fact(_make_fact(rec.memory_id))

    stop = threading.Event()
    deadlock = threading.Event()
    all_errors: list[Exception] = []

    writer_progresses: list[list[int]] = [[0] for _ in range(N_WRITERS)]
    reader_progresses: list[list[int]] = [[0] for _ in range(N_READERS)]

    threads: list[threading.Thread] = []

    # Writers.
    for i in range(N_WRITERS):
        threads.append(threading.Thread(
            target=_writer_worker,
            args=(mgr, stop, writer_progresses[i], all_errors),
            daemon=True,
            name=f"writer-{i}",
        ))

    # Readers.
    for i in range(N_READERS):
        threads.append(threading.Thread(
            target=_reader_worker,
            args=(mgr, stop, reader_progresses[i], all_errors),
            daemon=True,
            name=f"reader-{i}",
        ))

    # Churn threads.
    for i in range(N_CHURN):
        threads.append(threading.Thread(
            target=_churn_worker,
            args=(mgr, stop, all_errors),
            daemon=True,
            name=f"churn-{i}",
        ))

    # Long-lived reader — uses raw sqlite3.connect(), NOT db._connect().
    threads.append(threading.Thread(
        target=_long_lived_reader_worker,
        args=(db_path, stop, all_errors),
        daemon=True,
        name="long-lived-reader",
    ))

    # Watchdog monitors combined writer + reader progress.
    all_tracked_progresses = writer_progresses + reader_progresses
    watchdog_thread = threading.Thread(
        target=_watchdog_worker,
        args=(all_tracked_progresses, deadlock, stop),
        daemon=True,
        name="watchdog",
    )

    # --- Start all workers and watchdog ---
    for t in threads:
        t.start()
    watchdog_thread.start()

    soak_start = time.monotonic()

    # Drive the soak for SOAK_SECONDS, breaking early if deadlock detected.
    soak_end = soak_start + SOAK_SECONDS
    while time.monotonic() < soak_end:
        if deadlock.is_set():
            break
        time.sleep(0.1)

    actual_elapsed = time.monotonic() - soak_start
    stop.set()

    # Hard join: all workers respect stop, so they unblock quickly.
    # Give STALL_TIMEOUT_S + 5s headroom for workers currently blocked in C.
    join_timeout = STALL_TIMEOUT_S + 5.0
    for t in threads:
        t.join(timeout=join_timeout)
    watchdog_thread.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Collect results
    # ------------------------------------------------------------------
    total_writer_ops = sum(p[0] for p in writer_progresses)
    total_reader_ops = sum(p[0] for p in reader_progresses)
    writer_ops_per_second = total_writer_ops / max(actual_elapsed, 1.0)
    reader_ops_per_second = total_reader_ops / max(actual_elapsed, 1.0)

    print(
        f"\n[concurrency_soak] "
        f"elapsed={actual_elapsed:.1f}s "
        f"writer_ops={total_writer_ops} ({writer_ops_per_second:.1f}/s) "
        f"reader_ops={total_reader_ops} ({reader_ops_per_second:.1f}/s) "
        f"per_writer={[p[0] for p in writer_progresses]} "
        f"per_reader={[p[0] for p in reader_progresses]}"
    )

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    # 1. Watchdog: no near-total stall.
    assert not deadlock.is_set(), (
        f"DEADLOCK DETECTED: all {N_WRITERS} writer + {N_READERS} reader threads "
        f"made zero combined progress for {STALL_TIMEOUT_S}s. "
        f"This indicates the WAL close-path convoy: a checkpoint-on-close "
        f"is blocking while holding the process-global VFS mutex, preventing "
        f"all concurrent sqlite3.connect() calls. "
        f"writer_ops={[p[0] for p in writer_progresses]}, "
        f"reader_ops={[p[0] for p in reader_progresses]}, "
        f"errors={all_errors[:3]}"
    )

    # 2. Forward progress: each writer must have completed at least MIN_WRITER_OPS.
    #    This is the PRIMARY discriminator between "fix active" and "fix absent".
    #    With fix: 50–300 ops/writer in 25s.
    #    Without fix: ~1–2 ops/writer (each op takes ~3×READER_HOLD_S=15s).
    for i, p in enumerate(writer_progresses):
        assert p[0] >= MIN_WRITER_OPS, (
            f"Writer {i} completed only {p[0]} ops in {actual_elapsed:.1f}s "
            f"(floor is {MIN_WRITER_OPS}). "
            f"Throughput collapse detected — likely WAL close-path convoy: "
            f"checkpoint-on-close blocking while the long-lived reader mark "
            f"({READER_HOLD_S}s hold) is pinned. "
            f"All writers: {[p[0] for p in writer_progresses]}. "
            f"Errors: {all_errors[:3]}"
        )

    # 3. No unhandled exceptions.
    assert not all_errors, (
        f"Worker exceptions (showing first 5): {all_errors[:5]}"
    )
