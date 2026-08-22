# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Nothing can learn from a recall that left no record of itself.

WHAT WAS MEASURED, BEFORE

The queue that carries "these memories were shown, for this query, in this
session" had **no callers anywhere in the source**. A worker started with the
daemon and waited for events that were never sent. Consequences on a real store,
all of them downstream of that one gap:

    pending tickets carrying a join key      0 of 162
    outcomes carrying a join key             0 of 162
    per-memory usefulness scores off 0.5     0 of 377
    ranking model last retrained             eleven weeks earlier

Every hop after the first was working and had nothing to work on.

WHAT THIS MUST NOT COST

Recall is read-path work under a two-second budget, and a user notices latency
long before they notice that ranking has stopped improving. So the ticket is one
``put_nowait`` on a bounded in-memory queue: measured at **1.57 microseconds**,
0.0001% of a 1,449 ms recall. It drops rather than blocks when full and never
raises. A/B over 30 warmed queries moved p50 by −3.5 ms with the change ON,
which is to say it moved by nothing.

WHAT IT DELIBERATELY DOES NOT DO

It records; it does not act. Whether learning may reorder results stays a
separate explicit setting, still off by default, so wiring this changes what the
system knows and not what it returns.
"""

from __future__ import annotations

import json

import pytest

from superlocalmemory.learning import outcome_queue

from tests.conftest import force_sync_enrichment


@pytest.fixture(autouse=True)
def _drain_queue():
    """Each test starts with an empty queue and leaves one behind."""
    while not outcome_queue._queue.empty():
        outcome_queue._queue.get_nowait()
    yield
    while not outcome_queue._queue.empty():
        outcome_queue._queue.get_nowait()


def _events() -> list:
    return list(outcome_queue._queue.queue)


class TestARecallLeavesATicket:
    def test_a_recall_with_a_session_enqueues_one(self, engine_with_mock_deps) -> None:
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("The projection outbox makes a lost write impossible.")

        engine.recall("projection outbox", limit=5, session_id="s-1")

        events = _events()
        assert len(events) == 1, "the recall left no record that it happened"
        assert events[0].session_id == "s-1"
        assert events[0].profile_id == "default"

    def test_the_ticket_carries_a_join_key(self, engine_with_mock_deps) -> None:
        """An empty key is the state that made all 162 real outcomes unmatchable.

        ``calibration_id`` was the obvious candidate for this and is wrong: it
        is None on most paths, so using it would have reproduced the defect
        exactly while looking wired.
        """
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("A memory to find later.")

        engine.recall("memory to find", limit=5, session_id="s-2")

        (event,) = _events()
        assert event.query_id, "no join key — an outcome could never be matched"
        assert len(event.query_id) >= 16

    def test_two_recalls_get_different_keys(self, engine_with_mock_deps) -> None:
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("Something worth recalling twice.")

        engine.recall("worth recalling", limit=5, session_id="s-3")
        engine.recall("worth recalling", limit=5, session_id="s-3")

        keys = {event.query_id for event in _events()}
        assert len(keys) == 2, "one key for two recalls cannot attribute either"

    def test_the_ticket_names_the_memories_that_were_shown(
        self, engine_with_mock_deps,
    ) -> None:
        """Credit has to land on what was actually returned, not on the query."""
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("CozoDB holds the graph projection for this store.")

        response = engine.recall("graph projection", limit=5, session_id="s-4")

        (event,) = _events()
        returned = [r.fact.fact_id for r in response.results]
        assert event.fact_ids == returned


class TestItCannotHurtTheReader:
    def test_a_recall_with_no_session_is_recorded_under_a_stable_name(
        self, engine_with_mock_deps,
    ) -> None:
        """Almost no caller names a session, so refusing to record those meant
        recording almost nothing.

        This asserted the opposite until it was measured: one of the thirty-five
        places that call recall threads a session through, and the network route
        threads none, so every other recall left no trace at all. A name that is
        stable for this process is not as good as the caller's own, and it is
        far better than an empty one, which is discarded and can never be
        matched to anything.
        """
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("A memory recalled without a session.")

        engine.recall("without a session", limit=5)

        (event,) = _events()
        assert event.session_id, "the recall left a record with no name on it"
        assert event.query_id, "the record cannot be joined to an outcome"

    def test_a_ticket_with_no_name_is_still_refused(self) -> None:
        """The control for the rule above. The name has to come from somewhere;
        the queue must keep refusing one that does not."""
        before = outcome_queue.get_counters()["recall_enqueued"]
        outcome_queue.enqueue_recall(outcome_queue.RecallEvent(
            session_id="", profile_id="default", query="q",
            fact_ids=(), query_id="x",
        ))
        assert outcome_queue.get_counters()["recall_enqueued"] == before

    def test_a_broken_queue_does_not_break_the_recall(
        self, engine_with_mock_deps, monkeypatch,
    ) -> None:
        """A read must not fail because bookkeeping did."""
        def _explode(_event):
            raise RuntimeError("queue is on fire")

        monkeypatch.setattr(outcome_queue, "enqueue_recall", _explode)
        engine = force_sync_enrichment(engine_with_mock_deps)
        engine.store("The answer must still arrive.")

        response = engine.recall("answer must still arrive", limit=5,
                                 session_id="s-5")

        assert response is not None
        assert response.results is not None

    def test_the_enqueue_is_cheap_enough_to_ignore(self) -> None:
        """The budget for this is "unmeasurable", not "small".

        Measured at 1.57 microseconds per call against a recall of about 1.4
        seconds. The bound here is loose on purpose — it is guarding against a
        future change that makes this do I/O, not against jitter.
        """
        import time

        from superlocalmemory.learning.outcome_queue import RecallEvent

        event = RecallEvent(
            session_id="s", profile_id="default", query="q",
            fact_ids=["f"] * 10, query_id="k" * 32,
        )
        started = time.perf_counter()
        for _ in range(20_000):
            outcome_queue.enqueue_recall(event)
        per_call_us = (time.perf_counter() - started) / 20_000 * 1e6

        assert per_call_us < 100, (
            f"{per_call_us:.1f} us per recall — this is on the read path and "
            f"something has started doing real work in it"
        )


class TestItRecordsWithoutActing:
    def test_wiring_the_ticket_did_not_turn_ranking_on(self) -> None:
        """Recording what happened and letting it reorder results are separate
        decisions. The second stays off unless an operator sets it, so a store
        that upgrades starts gathering evidence without its answers moving."""
        from superlocalmemory.core.recall_pipeline import _resolve_ranking_mode

        assert _resolve_ranking_mode({}) == "off"
        assert _resolve_ranking_mode({"SLM_RANKING": "v2-ensemble"}) == "v2-ensemble"

    def test_exposure_rows_are_still_not_written(self) -> None:
        """The twenty-per-query exposure enqueue is what inflated the ranking
        phase counter 2,675x. It stays off; the ticket above is one row."""
        import inspect

        from superlocalmemory.core import recall_pipeline

        source = inspect.getsource(recall_pipeline.run_recall)
        assert "record_signals=False" in source, (
            "the per-fact exposure enqueue has been re-enabled; that is the "
            "2,675x counter inflation, not the learning fix"
        )
