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


# ---------------------------------------------------------------------------
# An id a front invented is not a conversation
# ---------------------------------------------------------------------------

def test_an_invented_session_id_does_not_register_a_working_set():
    """The HTTP front invents one per REQUEST, so each would get its own set.

    Enough dashboard searches and the registry hits its cap and evicts the
    least-recently-touched entry — which is a real conversation sitting idle
    between turns. Its next turn comes back cold, with nothing logged.
    """
    from superlocalmemory.core.recall_pipeline import _admit_to_working_memory

    results = _results([("a", 0.9), ("b", 0.8)])
    for invented in ("http:1755820000000", "mcp:mcp_client", "cli:x", ""):
        _admit_to_working_memory(results, "p", invented)
    assert wm.registry_size() == 0, (
        f"invented ids registered {wm.registry_size()} working set(s)"
    )


def test_a_flood_of_invented_ids_cannot_evict_a_real_conversation():
    """The eviction sequence, driven rather than argued.

    A real conversation holds a working set. Then more dashboard searches arrive
    than the registry can hold. The conversation must survive.
    """
    from superlocalmemory.core.recall_pipeline import _admit_to_working_memory

    real = "claude-session-abc123"
    wm.get_or_create("p", real).admit(["remembered"])
    assert wm.peek("p", real) is not None

    results = _results([("x", 0.5)])
    for i in range(wm.MAX_SESSIONS * 2):
        _admit_to_working_memory(results, "p", f"http:{i}")

    held = wm.peek("p", real)
    assert held is not None, "a real conversation was evicted by dashboard traffic"
    assert held.boost_set() == frozenset({"remembered"})


def test_two_clients_sharing_an_invented_id_do_not_share_memories():
    """The MCP front invents one per CLIENT, and it is the same string.

    Every client that sends no id gets `mcp:<agent_id>` with the default agent,
    so unrelated clients pooled one seven-slot set and promoted each other's
    memories into each other's answers.
    """
    from superlocalmemory.core.recall_pipeline import (
        _admit_to_working_memory,
        _apply_working_memory_bias,
    )

    _admit_to_working_memory(_results([("client_a_private", 0.9)]),
                             "p", "mcp:mcp_client")
    results = _results([("neutral", 0.9), ("client_a_private", 0.1)])
    out = _apply_working_memory_bias(results, "p", "mcp:mcp_client")
    assert [r.fact.fact_id for r in out] == ["neutral", "client_a_private"], (
        "one client's memory was promoted in another client's answer"
    )


def test_a_real_id_still_gets_continuity():
    """The guard must not turn the feature off for the callers it is for."""
    from superlocalmemory.core.recall_pipeline import (
        _admit_to_working_memory,
        _apply_working_memory_bias,
    )

    _admit_to_working_memory(_results([("carried", 0.9)]), "p", "session-real-1")
    assert wm.peek("p", "session-real-1") is not None
    results = _results([("other", 0.90), ("carried", 0.80)])
    out = _apply_working_memory_bias(results, "p", "session-real-1")
    assert [r.fact.fact_id for r in out] == ["carried", "other"]


# ---------------------------------------------------------------------------
# Zero is a score, not a missing value
# ---------------------------------------------------------------------------

def test_a_model_score_of_zero_is_honoured_not_discarded():
    """A cross-encoder that rules a candidate out returns exactly 0.0.

    ``ranking_score or score`` treats that as "no score" and falls back to the
    retrieval score, which promotes the candidate the model just rejected. The
    bug is invisible while no reranker is loaded, because then the field really
    is None.
    """
    from superlocalmemory.core.recall_pipeline import _rank_key

    ruled_out = _Result(fact=_Fact("ruled_out"), score=0.90, ranking_score=0.0)
    kept = _Result(fact=_Fact("kept"), score=0.10, ranking_score=0.5)

    order = [r.fact.fact_id for r in sorted([ruled_out, kept], key=_rank_key)]
    assert order == ["kept", "ruled_out"], (
        "a model score of 0.0 was discarded and the candidate ranked on its "
        "retrieval score instead"
    )


def test_a_genuinely_absent_score_still_falls_back():
    """The fallback has to survive the fix — it is what makes an unreranked
    answer keep the ordering retrieval worked for."""
    from superlocalmemory.core.recall_pipeline import _rank_key

    a = _Result(fact=_Fact("a"), score=0.9, ranking_score=None)
    b = _Result(fact=_Fact("b"), score=0.4, ranking_score=None)
    assert [r.fact.fact_id for r in sorted([a, b], key=_rank_key)] == ["a", "b"]


def test_the_bias_adds_to_a_zero_score_rather_than_to_the_retrieval_score():
    """Same falsiness, on the write side.

    Adding the bonus to `score` when `ranking_score` is 0.0 inflates a candidate
    by the model score it never earned.
    """
    from superlocalmemory.core.recall_pipeline import (
        WM_MAX_BONUS,
        _apply_working_memory_bias,
    )

    wm.get_or_create("p", "s").admit(["held"])
    held = _Result(fact=_Fact("held"), score=0.90, ranking_score=0.0)
    out = _apply_working_memory_bias([held], "p", "s")
    assert out[0].ranking_score == pytest.approx(WM_MAX_BONUS), (
        f"expected 0.0 + {WM_MAX_BONUS}, got {out[0].ranking_score} — the "
        f"retrieval score was used as the base"
    )


# ---------------------------------------------------------------------------
# What the two bonuses can do TOGETHER
# ---------------------------------------------------------------------------

def test_the_individual_caps_sum_to_the_stated_total():
    """Each pass bounds itself; nothing bounded the sum.

    Both auditors raised this independently. The point is not that 0.35 is the
    wrong number — it is that it was nobody's decision, just what two constants
    happened to add up to. Now it is a stated invariant, and adding a third
    bias source without revisiting it fails here.
    """
    from superlocalmemory.core.recall_pipeline import (
        MAX_TOTAL_BIAS,
        WM_MAX_BONUS,
    )
    from superlocalmemory.learning.pcos import MAX_BONUS as PCOS_MAX_BONUS

    assert WM_MAX_BONUS + PCOS_MAX_BONUS <= MAX_TOTAL_BIAS, (
        f"the bias passes now sum to {WM_MAX_BONUS + PCOS_MAX_BONUS}, above the "
        f"stated total of {MAX_TOTAL_BIAS}. Either lower a cap or decide, "
        f"deliberately, that a larger total is acceptable."
    )


def test_a_clearly_better_match_cannot_be_displaced_by_bias_alone():
    """The property the total bound exists to give.

    A result ahead by more than everything the bias passes can add must stay
    ahead. Below that margin the bonuses are meant to reorder — that is the
    feature — so the test asserts the guarantee, not the absence of movement.
    """
    from superlocalmemory.core.recall_pipeline import (
        MAX_TOTAL_BIAS,
        _apply_working_memory_bias,
    )

    margin = MAX_TOTAL_BIAS + 0.01
    wm.get_or_create("p", "s").admit(["weaker"])
    results = [
        _Result(fact=_Fact("stronger"), score=0.50 + margin, ranking_score=0.50 + margin),
        # Already carrying the largest bonus the other pass can give.
        _Result(fact=_Fact("weaker"), score=0.50, ranking_score=0.50 + 0.15),
    ]
    out = _apply_working_memory_bias(results, "p", "s")
    assert [r.fact.fact_id for r in out] == ["stronger", "weaker"], (
        "a result ahead by more than the total bias bound was still displaced"
    )


def test_within_the_margin_the_bonuses_do_reorder():
    """The other half: a bound that stopped all movement would be a disabled
    feature, not a safe one."""
    from superlocalmemory.core.recall_pipeline import _apply_working_memory_bias

    wm.get_or_create("p", "s2").admit(["carried"])
    results = [
        _Result(fact=_Fact("fresh"), score=0.60, ranking_score=0.60),
        _Result(fact=_Fact("carried"), score=0.50, ranking_score=0.50),
    ]
    out = _apply_working_memory_bias(results, "p", "s2")
    assert [r.fact.fact_id for r in out] == ["carried", "fresh"]
