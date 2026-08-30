# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A small, per-session set of memories that biases the next recall.

WHAT THIS IS FOR
----------------
Turn 5 of a conversation currently knows nothing about turns 1-4. Every recall
starts from the query alone, so a memory the user was just discussing competes
from scratch against every other memory in the store.

``ContextCache`` does not fix this, because it does something different: on a
hit it answers *instead of* searching, and on a miss it contributes nothing.
This module biases a search that always runs in full. The distinction is the
whole point — a cache replaces retrieval, a working set shapes it.

The two are complementary and both stay: the cache short-circuits an exact
repeat of a query before the daemon is reached, this biases the ranking of
every query that does reach it.

WHY SO SMALL
------------
Seven slots per session, evicting the least-activated when full. Human working
memory is capacity-limited to a handful of items, and the limit is what makes it
useful: an unbounded "everything recently seen" set would boost most of the
store and therefore rank nothing.

Seven is the constant this ships with. It is a starting point, not a derived
optimum, and ``MAX_SLOTS`` is the single place to change it.

ADMISSION IS BY RANK, NOT BY SCORE
----------------------------------
An earlier design admitted a memory when its fused score cleared 0.60. That
threshold is unreachable, and measurably so: reciprocal-rank fusion of *n*
channels caps the top score at ``n/(k+1)``, which is 0.31 at ``rrf_k=15`` and
0.08 at ``rrf_k=60``. Nothing would ever have been admitted, the bias would
never have fired, and the symptom would have been silence rather than an error.
(The 0.60 that does exist in retrieval is the evidence floor's semantic cosine —
a different quantity on a different scale.)

So admission takes the memories that were actually *shown* at the top of an
answer. Rank survives every rescaling the pipeline applies to scores; an
absolute threshold does not.

WHY A LOCK, AND NOT "THE GIL IS ENOUGH"
---------------------------------------
``admit`` reads the slot list, decides an eviction, and writes it back. That is
not atomic under the GIL — it spans many bytecodes, and the daemon serves
recalls from a thread-pool executor, so two recalls in one session genuinely
interleave. Without the lock, both can pass the capacity check and both append,
leaving eight slots in a seven-slot set. A lock acquisition costs nanoseconds
against a recall measured in hundreds of milliseconds.

NEVER PERSISTED
---------------
There is no table behind this and there must not be. It is ephemeral by
definition; persisting it would create an unbounded write per recall for state
whose entire value is that it is cheap.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

__all__ = [
    "MAX_SLOTS",
    "WorkingMemory",
    "discard",
    "discard_profile",
    "get_or_create",
    "registry_size",
]

#: Slots per session. See "WHY SO SMALL" above.
MAX_SLOTS = 7

#: How many shown memories a single answer may offer for admission. Matches the
#: prefix the settlement path records as "what this query showed", so the two
#: notions of "what the user actually saw" cannot drift apart.
ADMIT_TOP_N = 5

#: A session untouched for this long is dropped. The daemon runs for weeks.
SESSION_IDLE_EVICT_SECS = 86_400  # 24 h

#: Hard ceiling on tracked sessions, evicting least-recently-touched first.
#: Age alone does not bound anything inside the first 24 hours, and a burst of
#: short-lived session ids is exactly the shape of an agent workload.
MAX_SESSIONS = 512

#: Activation halves roughly every hour of disuse.
_RECENCY_HALFLIFE_SECS = 3_600.0


@dataclass(frozen=True)
class _Slot:
    """One remembered memory. Frozen, so an update is a replacement.

    Immutability is not decoration here: a reader holding this object while a
    writer updates the same memory sees either the old slot or the new one,
    never a half-applied mix of the two.
    """

    fact_id: str
    hits: int
    last_seen: float

    def activation(self, now: float) -> float:
        """How strongly this memory is currently held.

        Repetition raises it, elapsed time lowers it. The decay is a smooth
        reciprocal rather than a step, so eviction never depends on which side
        of a boundary a timestamp happened to land.
        """
        age = max(0.0, now - self.last_seen)
        return self.hits / (1.0 + age / _RECENCY_HALFLIFE_SECS)


class WorkingMemory:
    """The working set for one (profile, session) pair."""

    __slots__ = ("_lock", "_slots", "_touched_at")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}
        self._touched_at = time.monotonic()

    # -- mutation ----------------------------------------------------------

    def admit(self, fact_ids: list[str] | tuple[str, ...]) -> None:
        """Offer the memories an answer just showed, best-ranked first.

        Already-held memories are reinforced rather than duplicated, which is
        what makes a memory referenced across several turns hard to evict.
        """
        if not fact_ids:
            return
        now = time.monotonic()
        with self._lock:
            self._touched_at = now
            for fact_id in fact_ids[:ADMIT_TOP_N]:
                if not fact_id:
                    continue
                existing = self._slots.get(fact_id)
                self._slots[fact_id] = _Slot(
                    fact_id=fact_id,
                    hits=(existing.hits + 1) if existing else 1,
                    last_seen=now,
                )
            self._evict_locked(now)

    def touch(self, fact_id: str) -> None:
        """Reinforce a memory something downstream actually used.

        Stronger evidence than having been displayed, and it arrives later, so
        it is a separate entry point rather than a flag on ``admit``. A memory
        that is not held is not admitted by being touched: this reinforces
        attention, it does not create it.
        """
        if not fact_id:
            return
        now = time.monotonic()
        with self._lock:
            self._touched_at = now
            existing = self._slots.get(fact_id)
            if existing is None:
                return
            self._slots[fact_id] = _Slot(
                fact_id=fact_id, hits=existing.hits + 1, last_seen=now,
            )

    def _evict_locked(self, now: float) -> None:
        """Drop the least-activated slots. Caller holds the lock."""
        while len(self._slots) > MAX_SLOTS:
            weakest = min(
                self._slots.values(),
                key=lambda s: (s.activation(now), s.last_seen, s.fact_id),
            )
            self._slots.pop(weakest.fact_id, None)

    # -- reads -------------------------------------------------------------

    def boost_set(self) -> frozenset[str]:
        """The memories currently held. Read by the ranking bias."""
        with self._lock:
            return frozenset(self._slots)

    def idle_secs(self, now: float | None = None) -> float:
        with self._lock:
            return max(0.0, (now or time.monotonic()) - self._touched_at)

    def __len__(self) -> int:
        with self._lock:
            return len(self._slots)


# ---------------------------------------------------------------------------
# Registry
#
# Keyed by (profile_id, session_id), never by session_id alone. Two profiles in
# one daemon can be handed the same session id, and a shared key would let one
# profile's memories bias the other's answers. It also gives erasure something
# to erase: ``discard_profile`` can find every set belonging to a profile.
# ---------------------------------------------------------------------------

_REGISTRY: dict[tuple[str, str], WorkingMemory] = {}
_REGISTRY_LOCK = threading.Lock()


def get_or_create(profile_id: str, session_id: str) -> WorkingMemory:
    """The working set for this session, creating it on first use.

    Both arguments are required and neither defaults. A default ``profile_id``
    is how two profiles end up sharing one set.
    """
    key = (profile_id or "", session_id or "")
    with _REGISTRY_LOCK:
        _sweep_locked()
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing
        created = WorkingMemory()
        _REGISTRY[key] = created
        return created


def peek(profile_id: str, session_id: str) -> WorkingMemory | None:
    """The working set if one exists, without creating one.

    Reading must not allocate: a bias that runs on every recall would otherwise
    register an entry for every session id it ever sees, including those that
    never admit anything.
    """
    with _REGISTRY_LOCK:
        return _REGISTRY.get((profile_id or "", session_id or ""))


def discard(profile_id: str, session_id: str) -> None:
    """Forget one session. Called when a session closes."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop((profile_id or "", session_id or ""), None)


def discard_profile(profile_id: str) -> int:
    """Forget every session belonging to a profile. Returns the count dropped.

    Called from erasure. In-process state is still the erased subject's data:
    leaving it behind would let a deleted profile's memories bias a later
    session that reuses its session id, which is the residue defect this
    project has already paid for once in a search index.
    """
    target = profile_id or ""
    with _REGISTRY_LOCK:
        doomed = [k for k in _REGISTRY if k[0] == target]
        for key in doomed:
            _REGISTRY.pop(key, None)
        return len(doomed)


def registry_size() -> int:
    with _REGISTRY_LOCK:
        return len(_REGISTRY)


def _sweep_locked() -> None:
    """Bound the registry by idle time and then by count. Caller holds the lock."""
    now = time.monotonic()
    stale = [
        key for key, wm in _REGISTRY.items()
        if wm.idle_secs(now) > SESSION_IDLE_EVICT_SECS
    ]
    for key in stale:
        _REGISTRY.pop(key, None)
    # Trim to one below the ceiling, because the caller is about to insert.
    # Trimming to the ceiling itself leaves the registry one over after every
    # insertion, which is a cap that is never actually enforced. Sweeping after
    # the insert instead would make the new entry — idle for zero seconds, tied
    # with every other new entry — a candidate for its own eviction.
    headroom = MAX_SESSIONS - 1
    if len(_REGISTRY) <= headroom:
        return
    by_age = sorted(
        _REGISTRY.items(), key=lambda kv: (-kv[1].idle_secs(now), kv[0]),
    )
    for key, _wm in by_age[: len(_REGISTRY) - headroom]:
        _REGISTRY.pop(key, None)
