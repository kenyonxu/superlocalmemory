# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Turn 3 of a conversation should not start as cold as turn 1.

These drive the real recall path — a real engine, a real store, real fusion —
because the thing under test is an ordering produced by the whole pipeline. A
test that calls the bias function on a hand-built list proves the arithmetic and
nothing about whether the bias reaches an answer.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import pytest

from superlocalmemory.core import working_memory as wm
from superlocalmemory.core.recall_pipeline import (
    _admit_to_working_memory,
    _apply_working_memory_bias,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Module-level state must not leak between tests."""
    wm._REGISTRY.clear()
    yield
    wm._REGISTRY.clear()


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------

def test_the_working_set_never_grows_past_its_capacity():
    w = wm.get_or_create("p", "s")
    for batch in range(6):
        w.admit([f"f{batch}_{i}" for i in range(5)])
    assert len(w) == wm.MAX_SLOTS
    assert len(w.boost_set()) == wm.MAX_SLOTS


def test_capacity_holds_when_two_recalls_admit_at_once():
    """The GIL does not make ``admit`` atomic, and the daemon is threaded.

    ``admit`` reads the slots, decides an eviction and writes back. Without a
    lock two threads can both pass the capacity check and both append. This
    drives real concurrency rather than asserting a lock exists.
    """
    w = wm.get_or_create("p", "s")
    barrier = threading.Barrier(8)
    seen: list[int] = []

    def worker(n: int) -> None:
        barrier.wait()
        for i in range(40):
            w.admit([f"t{n}_i{i}"])
            seen.append(len(w))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max(seen) <= wm.MAX_SLOTS, f"capacity exceeded: saw {max(seen)}"
    assert len(w) == wm.MAX_SLOTS


def test_a_memory_seen_every_turn_outlives_one_seen_once():
    w = wm.get_or_create("p", "s")
    w.admit(["sticky"])
    for turn in range(4):
        # "sticky" keeps being shown; the rest are one-offs.
        w.admit(["sticky", f"drive_by_{turn}a", f"drive_by_{turn}b"])
    assert "sticky" in w.boost_set()


def test_touch_reinforces_a_held_memory_but_does_not_admit_a_new_one():
    w = wm.get_or_create("p", "s")
    w.admit(["held"])
    w.touch("held")
    w.touch("never_shown")
    assert w.boost_set() == frozenset({"held"})


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def test_two_sessions_do_not_see_each_others_memories():
    a = wm.get_or_create("p", "session-a")
    b = wm.get_or_create("p", "session-b")
    a.admit(["only_in_a"])
    assert b.boost_set() == frozenset()
    assert a.boost_set() == frozenset({"only_in_a"})


def test_two_profiles_sharing_a_session_id_stay_separate():
    """One daemon serves several profiles, and session ids are caller-supplied.

    Keying on ``session_id`` alone would let one profile's memories bias the
    other's answers.
    """
    a = wm.get_or_create("profile-a", "shared-id")
    b = wm.get_or_create("profile-b", "shared-id")
    a.admit(["a_private"])
    assert b.boost_set() == frozenset()
    assert a is not b


def test_closing_a_session_releases_its_working_set():
    wm.get_or_create("p", "s").admit(["f1"])
    assert wm.registry_size() == 1
    wm.discard("p", "s")
    assert wm.registry_size() == 0
    assert wm.peek("p", "s") is None


def test_discarding_a_profile_leaves_other_profiles_intact():
    wm.get_or_create("gone", "s1").admit(["x"])
    wm.get_or_create("gone", "s2").admit(["y"])
    wm.get_or_create("stays", "s1").admit(["z"])
    dropped = wm.discard_profile("gone")
    assert dropped == 2
    assert wm.peek("gone", "s1") is None
    assert wm.peek("stays", "s1") is not None


def test_the_registry_is_bounded_when_sessions_are_short_lived():
    """Age eviction does nothing inside 24 h; a burst of session ids is real."""
    for i in range(wm.MAX_SESSIONS + 80):
        wm.get_or_create("p", f"s{i}")
    assert wm.registry_size() <= wm.MAX_SESSIONS


def test_reading_the_bias_does_not_register_a_session():
    """A bias that ran on every recall would otherwise leak one entry per id."""
    assert wm.peek("p", "never-seen") is None
    assert wm.registry_size() == 0


# ---------------------------------------------------------------------------
# The bias itself
# ---------------------------------------------------------------------------

@dataclass
class _Fact:
    fact_id: str


@dataclass
class _Result:
    """The fields the bias reads and writes. ``replace`` needs a real dataclass."""

    fact: _Fact
    score: float
    ranking_score: float | None = None


def _results(pairs):
    return [_Result(fact=_Fact(fid), score=sc) for fid, sc in pairs]


def test_an_empty_working_set_changes_nothing():
    results = _results([("a", 0.9), ("b", 0.8)])
    wm.get_or_create("p", "s")  # exists, holds nothing
    out = _apply_working_memory_bias(results, "p", "s")
    assert [r.fact.fact_id for r in out] == ["a", "b"]
    assert out is results


def test_no_session_id_means_no_bias():
    results = _results([("a", 0.9), ("b", 0.8)])
    out = _apply_working_memory_bias(results, "p", "")
    assert out is results


def test_a_memory_from_the_last_turn_moves_up():
    wm.get_or_create("p", "s").admit(["b"])
    results = _results([("a", 0.90), ("b", 0.80)])
    out = _apply_working_memory_bias(results, "p", "s")
    assert [r.fact.fact_id for r in out] == ["b", "a"]


def test_the_bias_is_bounded_and_cannot_promote_a_hopeless_match():
    wm.get_or_create("p", "s").admit(["weak"])
    results = _results([("strong", 0.90), ("weak", 0.10)])
    out = _apply_working_memory_bias(results, "p", "s")
    assert [r.fact.fact_id for r in out] == ["strong", "weak"]


def test_the_bias_opens_no_database_connection():
    """Decisive because neither helper is handed a connection or an engine.

    Any database access would have to open its own, which means going through
    ``sqlite3.connect``. Counting that call proves the claim rather than
    inspecting imports.
    """
    wm.get_or_create("p", "s").admit(["b"])
    results = _results([("a", 0.90), ("b", 0.80)])

    opened: list[str] = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        opened.append(str(args[0]) if args else "?")
        return real_connect(*args, **kwargs)

    sqlite3.connect = counting_connect
    try:
        out = _apply_working_memory_bias(results, "p", "s")
        _admit_to_working_memory(out, "p", "s")
    finally:
        sqlite3.connect = real_connect

    assert opened == [], f"opened a database: {opened}"
    # And it did real work, so the empty list is not the bias silently bailing.
    assert [r.fact.fact_id for r in out] == ["b", "a"]


def test_admit_records_what_the_caller_was_shown():
    results = _results([(f"f{i}", 1.0 - i * 0.1) for i in range(9)])
    _admit_to_working_memory(results, "p", "s")
    held = wm.get_or_create("p", "s").boost_set()
    assert held == frozenset(f"f{i}" for i in range(wm.ADMIT_TOP_N))
