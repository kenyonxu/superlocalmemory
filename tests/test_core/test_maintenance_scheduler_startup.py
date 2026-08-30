# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for the M048 startup self-heal timer in core/maintenance_scheduler.py.

Regression coverage for the 2026-08-24 fix: M048's re-read pass is idempotent
and safe to replay (see its own verify() docstring), but the migration
runner never replays a completed migration, and the periodic maintenance
cycle only reaches it every ``scheduler_interval_minutes`` (360 by default).
A store that drifted since the last cycle stayed blocking — ready=false,
migrations=false in /health — for up to that whole interval after every
restart. Reproduced live: three separate restarts in one session each hit
"schema incomplete for completed migration M048...; automatic replay is
disabled" and required a manual fix. This adds a third staggered one-shot
startup timer (matching the existing cache-GC and graph-metrics ones) so a
drifted store converges within seconds of boot instead.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.core.maintenance_scheduler import MaintenanceScheduler


@pytest.fixture
def scheduler() -> MaintenanceScheduler:
    db = MagicMock()
    db.raw_connection = MagicMock()
    config = MagicMock()
    config.forgetting.scheduler_interval_minutes = 360
    return MaintenanceScheduler(db, config, profile_id="default")


def test_start_schedules_a_staggered_reclassify_timer(scheduler) -> None:
    scheduler.start()
    try:
        timer = scheduler._initial_reclassify_timer
        assert isinstance(timer, threading.Timer)
        assert timer.interval == 210.0
        assert timer.function == scheduler._initial_reclassify_upcoming
        assert timer.daemon is True
    finally:
        scheduler.stop()


def test_stop_cancels_the_reclassify_timer_before_it_fires(scheduler) -> None:
    scheduler.start()
    timer = scheduler._initial_reclassify_timer
    scheduler.stop()
    assert scheduler._initial_reclassify_timer is None
    # cancel() sets Timer.finished — the documented, non-racy signal that
    # the callback will not run. is_alive() is racy here: the underlying
    # thread can still be winding down immediately after cancel() returns.
    assert timer.finished.is_set()


def test_stop_is_idempotent_without_a_prior_start(scheduler) -> None:
    # Must not raise even though _initial_reclassify_timer was never set.
    scheduler.stop()


def test_initial_reclassify_calls_m048_apply_with_the_connection_factory(
    scheduler,
) -> None:
    with patch(
        "superlocalmemory.storage.migrations."
        "M048_upcoming_holds_only_what_is_upcoming.apply",
    ) as mock_apply:
        scheduler._running = True
        scheduler._initial_reclassify_upcoming()
        mock_apply.assert_called_once_with(
            open_connection=scheduler._db.raw_connection,
        )


def test_initial_reclassify_is_a_noop_after_stop(scheduler) -> None:
    with patch(
        "superlocalmemory.storage.migrations."
        "M048_upcoming_holds_only_what_is_upcoming.apply",
    ) as mock_apply:
        scheduler._running = False
        scheduler._initial_reclassify_upcoming()
        mock_apply.assert_not_called()


def test_initial_reclassify_never_raises_on_apply_failure(scheduler) -> None:
    with patch(
        "superlocalmemory.storage.migrations."
        "M048_upcoming_holds_only_what_is_upcoming.apply",
        side_effect=RuntimeError("boom"),
    ):
        scheduler._running = True
        scheduler._initial_reclassify_upcoming()  # must not raise
