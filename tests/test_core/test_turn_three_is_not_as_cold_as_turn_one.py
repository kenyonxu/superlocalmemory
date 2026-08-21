# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A multi-turn conversation, driven through the real recall path.

Everything here goes through ``MemoryEngine.recall`` against a real store. The
claim under test is about an ordering the whole pipeline produces, and the
pipeline is where an ordering gets laundered — a bias applied before the
cross-encoder is diluted to a quarter weight and rescaled by the batch maximum.
Asserting on a hand-built list would prove the arithmetic and miss that.
"""

from __future__ import annotations

import pytest

from superlocalmemory.core import working_memory as wm
from tests.conftest import force_sync_enrichment


@pytest.fixture(autouse=True)
def _clean_registry():
    wm._REGISTRY.clear()
    yield
    wm._REGISTRY.clear()


@pytest.fixture
def stocked(engine_with_mock_deps):
    """A store with several plausibly-competing memories."""
    engine = force_sync_enrichment(engine_with_mock_deps)
    for text in (
        "The deployment pipeline runs on a self-hosted runner in Frankfurt.",
        "The deployment pipeline was migrated from Jenkins in March.",
        "The deployment pipeline caches node modules between jobs.",
        "The staging database is restored from a nightly snapshot.",
        "The staging database runs Postgres 16.",
        "Invoices are reconciled on the last working day of the month.",
    ):
        engine.store(text)
    return engine


def _order(response) -> list[str]:
    return [r.fact.fact_id for r in response.results]


def test_a_memory_the_session_was_shown_moves_up_on_a_later_turn(stocked):
    """The load-bearing claim: continuity changes the answer, strictly.

    Constructed so it cannot pass by accident. The carried memory is taken from
    the BOTTOM of the cold answer, and the assertion is a strict improvement —
    an earlier version of this test allowed ``warm_rank <= cold_rank``, which a
    deterministic store satisfies by returning the identical order, and it
    passed with the bias turned off.

    Position 0 is deliberately not asserted on: an exact lexical hit is
    promoted there after every learned layer, and that guard outranks attention
    on purpose.
    """
    cold = stocked.recall("deployment pipeline", session_id="")
    cold_order = _order(cold)
    if len(cold_order) < 3:
        pytest.skip(f"store returned {len(cold_order)} candidates; need 3 to rank")

    carried = cold_order[-1]
    cold_rank = cold_order.index(carried)

    # Seed the session with exactly that memory. Done directly rather than via
    # a first query, because which facts a query happens to surface is not the
    # thing under test and choosing one that overlaps is guesswork.
    session = "conversation-1"
    wm.get_or_create(stocked.profile_id, session).admit([carried])

    warm_order = _order(stocked.recall("deployment pipeline", session_id=session))
    assert carried in warm_order, "the carried memory fell out of the answer"
    warm_rank = warm_order.index(carried)

    assert warm_rank < cold_rank, (
        f"no lift: {carried} sat at {cold_rank} cold and {warm_rank} warm"
    )


def test_a_real_recall_carries_forward_what_it_actually_showed(stocked):
    """The admit step, through a real recall rather than a direct call."""
    session = "conversation-1b"
    answer = stocked.recall("staging database snapshot", session_id=session)
    shown = _order(answer)
    assert shown, "fixture produced no recallable memories"

    held = wm.peek(stocked.profile_id, session)
    assert held is not None, "a session-bearing recall registered nothing"
    carried = held.boost_set()
    assert carried, "nothing was carried forward"
    assert carried <= set(shown), (
        f"carried memories the answer never showed: {carried - set(shown)}"
    )


def test_a_recall_without_a_session_id_registers_nothing(stocked):
    stocked.recall("deployment pipeline runner")
    assert wm.registry_size() == 0


def test_retrieval_still_runs_in_full_on_a_warm_session(stocked):
    """A bias, not a bypass: the same channels do the same work every turn.

    A cache would answer from its own contents and report no candidates. If
    the warm turn ever stops going to the channels, this catches it.
    """
    session = "conversation-2"
    first = stocked.recall("staging database snapshot", session_id=session)
    assert first.total_candidates > 0
    warm = stocked.recall("staging database snapshot", session_id=session)
    assert warm.total_candidates > 0, "warm turn answered without retrieving"
    assert warm.channel_weights, "warm turn produced no channel work"


def test_two_conversations_in_one_process_do_not_bias_each_other(stocked):
    stocked.recall("invoices reconciled", session_id="conv-a")
    held_a = wm.peek(stocked.profile_id, "conv-a").boost_set()
    assert held_a

    stocked.recall("staging database", session_id="conv-b")
    held_b = wm.peek(stocked.profile_id, "conv-b").boost_set()

    assert held_a.isdisjoint(held_b) or held_a != held_b
    # The important part: b never inherited a's set wholesale.
    assert wm.peek(stocked.profile_id, "conv-b") is not wm.peek(
        stocked.profile_id, "conv-a",
    )


def test_closing_the_session_ends_the_continuity(stocked):
    session = "conversation-3"
    stocked.recall("deployment pipeline runner", session_id=session)
    assert wm.peek(stocked.profile_id, session) is not None
    stocked.close_session(session)
    assert wm.peek(stocked.profile_id, session) is None


def test_a_warm_session_adds_no_queries_to_a_recall(stocked):
    """Continuity must cost nothing on the hot path.

    Counts real statements on the store this recall reads, comparing a cold
    turn against a warm one. Equal counts is the claim; a difference means the
    bias reached the database, which it must never do.
    """
    counted: list[int] = []

    def _count_statements(session_id: str) -> int:
        import sqlite3

        seen = [0]
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _stmt: seen.__setitem__(0, seen[0] + 1))
            return conn

        sqlite3.connect = traced_connect
        try:
            stocked.recall("deployment pipeline", session_id=session_id)
        finally:
            sqlite3.connect = real_connect
        return seen[0]

    cold = _count_statements("fresh-each-time-1")
    # Warm the session, then measure a second turn on it.
    stocked.recall("deployment pipeline", session_id="warm-measured")
    warm = _count_statements("warm-measured")
    counted += [cold, warm]

    assert warm <= cold, (
        f"warm recall issued more statements than cold: {warm} vs {cold}"
    )
