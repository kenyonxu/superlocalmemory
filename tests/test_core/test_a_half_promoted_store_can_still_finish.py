# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A store that got part-way through switching backends must be able to finish.

Promotion runs prepare -> verify -> promote, and the state is written down after
each step. Two of those three states used to be terminal by accident: the daemon
starts the backends only at ``promoted``, and the thing that would have finished
the promotion refused to look at anything except ``local_core``.

So a store that had built and checked its projection sat on SQLite forever while
its own configuration named Cozo and LanceDB as its backends. Measured on a real
store: ``scale_engine_state=verified`` with ``backend_status`` reporting
lancedb ``not_initialized`` and the graph answering from SQLite.

The promoter itself was always able to resume a half-finished stage. Only its
caller disagreed.
"""

from __future__ import annotations

import pytest

from superlocalmemory.core.backend_orchestrator import BackendOrchestrator


class _Config:
    def __init__(self, state: str) -> None:
        self.scale_engine_state = state
        self.scale_auto_promote_enabled = True


def _orchestrator(state: str, scheduled: list) -> BackendOrchestrator:
    orch = BackendOrchestrator.__new__(BackendOrchestrator)
    orch._config = _Config(state)
    return orch


@pytest.mark.parametrize("state", ["local_core", "prepared", "verified"])
def test_an_unfinished_promotion_is_scheduled(state: str, monkeypatch) -> None:
    started: list[str] = []

    class _Timer:
        def __init__(self, delay, fn):
            self.daemon = False

        def start(self):
            started.append(state)

    monkeypatch.setattr("threading.Timer", _Timer)
    _orchestrator(state, started)._maybe_schedule_auto_promote()

    assert started == [state], (
        f"a store at {state!r} was never offered a chance to finish promoting; "
        f"the daemon only starts the backends at 'promoted', so this state is "
        f"terminal and the store stays on SQLite forever"
    )


def test_a_finished_promotion_is_left_alone(monkeypatch) -> None:
    """The one state with nothing left to do.

    Without this the parametrised test above would pass just as well for a
    caller that scheduled unconditionally, which would rebuild a projection
    that is already serving.
    """
    started: list[str] = []

    class _Timer:
        def __init__(self, delay, fn):
            self.daemon = False

        def start(self):
            started.append("scheduled")

    monkeypatch.setattr("threading.Timer", _Timer)
    _orchestrator("promoted", started)._maybe_schedule_auto_promote()

    assert started == [], "an already-promoted store was asked to promote again"


def test_the_switch_is_still_the_operator_s_to_turn_off(monkeypatch) -> None:
    started: list[str] = []

    class _Timer:
        def __init__(self, delay, fn):
            self.daemon = False

        def start(self):
            started.append("scheduled")

    monkeypatch.setattr("threading.Timer", _Timer)
    orch = _orchestrator("verified", started)
    orch._config.scale_auto_promote_enabled = False
    orch._maybe_schedule_auto_promote()

    assert started == [], "auto-promotion ran with the setting turned off"


# ---------------------------------------------------------------------------
# The timer that the scheduler arms carries its own copy of the same rule.
# Fixing one and not the other arms a timer whose callback refuses.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["local_core", "prepared", "verified"])
def test_the_timer_callback_agrees_with_the_scheduler(state: str, monkeypatch) -> None:
    counted: list[str] = []

    orch = BackendOrchestrator.__new__(BackendOrchestrator)
    orch._config = _Config(state)
    # Reaching the edge count means the state gate let it through; stopping
    # there keeps this a test of the gate rather than of promotion.
    monkeypatch.setattr(
        BackendOrchestrator, "_count_default_edges",
        lambda self: counted.append(state) or 0,
    )
    orch._auto_promote_if_at_scale()

    assert counted == [state], (
        f"the timer fired for a store at {state!r} and its callback refused; "
        f"the scheduler and the callback must apply the same rule"
    )


def test_the_timer_callback_leaves_a_finished_store_alone(monkeypatch) -> None:
    counted: list[str] = []
    orch = BackendOrchestrator.__new__(BackendOrchestrator)
    orch._config = _Config("promoted")
    monkeypatch.setattr(
        BackendOrchestrator, "_count_default_edges",
        lambda self: counted.append("counted") or 0,
    )
    orch._auto_promote_if_at_scale()
    assert counted == [], "an already-promoted store was measured for promotion"
