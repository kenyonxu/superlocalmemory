"""Background upkeep that has stopped working must stop looking like a hiccup.

Every step in the maintenance cycle logs and continues, which is right: one
broken step must not stop the rest. But it meant a step that had been failing
for a week read exactly like one that had just hiccupped, and the daemon went
on reporting itself healthy the whole time.
"""

from __future__ import annotations

import logging


class _Scheduler:
    """The recorder alone, without the cycle around it."""

    from superlocalmemory.core.maintenance_scheduler import MaintenanceScheduler

    _ESCALATE_AFTER = MaintenanceScheduler._ESCALATE_AFTER
    _record_step = MaintenanceScheduler._record_step
    failing_steps = MaintenanceScheduler.failing_steps
    _note_step_outcomes = MaintenanceScheduler._note_step_outcomes


def test_one_failure_is_a_warning_not_an_alarm(caplog):
    scheduler = _Scheduler()
    with caplog.at_level(logging.WARNING):
        scheduler._record_step("retention", False, "locked")
    assert scheduler.failing_steps() == {"retention": 1}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_failing_every_cycle_is_reported_as_a_real_failure(caplog):
    scheduler = _Scheduler()
    with caplog.at_level(logging.WARNING):
        for _ in range(_Scheduler._ESCALATE_AFTER):
            scheduler._record_step("retention", False, "still locked")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, (
        f"a step that failed {_Scheduler._ESCALATE_AFTER} cycles running was "
        f"still only a warning"
    )
    assert "not a transient failure" in errors[-1].getMessage()


def test_a_step_that_recovers_stops_being_counted(caplog):
    scheduler = _Scheduler()
    scheduler._record_step("retention", False, "locked")
    scheduler._record_step("retention", False, "locked")
    assert scheduler.failing_steps()["retention"] == 2
    with caplog.at_level(logging.INFO):
        scheduler._record_step("retention", True)
    assert scheduler.failing_steps() == {}
    assert any("working again" in r.getMessage() for r in caplog.records)


def test_steps_are_counted_separately():
    scheduler = _Scheduler()
    scheduler._record_step("retention", False, "a")
    scheduler._record_step("graph metrics", False, "b")
    scheduler._record_step("retention", False, "a")
    assert scheduler.failing_steps() == {"retention": 2, "graph metrics": 1}


def test_the_cycle_records_the_outcome_of_each_step():
    """The counter is only worth having if the cycle feeds it."""
    import inspect

    from superlocalmemory.core.maintenance_scheduler import MaintenanceScheduler

    source = inspect.getsource(MaintenanceScheduler)
    recorded = source.count("self._record_step(")
    assert recorded >= 6, (
        f"only {recorded} calls record a step's outcome; the steps that do not "
        f"can fail forever without anything noticing"
    )
    assert "self._note_step_outcomes()" in source
