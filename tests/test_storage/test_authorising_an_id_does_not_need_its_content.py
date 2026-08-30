# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Deciding whether a memory may be seen must not cost decoding the memory.

Every retrieval channel re-authorises its candidates, because a candidate
generator may be a cache, an approximate index or a graph store rather than the
authorization source of truth. That check needs ids. It was answering with
``get_facts_by_ids``, which hydrates full ``AtomicFact`` objects — a 768-float
embedding and two 768-float Fisher vectors per row.

Measured on a copy of the author's store: the entity channel authorised 3,659
candidates to return a 20-result page, and that one call was **374 ms of a
430 ms recall — 87%**. The graph walk it was protecting cost 3.8 ms.

``visible_fact_ids`` answers the same question from ``SELECT fact_id``. These
tests exist to hold the two answers together: the fast path is only safe while
it agrees with the authorization source of truth on every input, including the
withheld and soft-deleted rows that are the whole reason the predicate exists.
"""

from __future__ import annotations

import pytest

from superlocalmemory.retrieval.scope_policy import authorized_fact_ids
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

_PARENT = "m-authz"


@pytest.fixture
def db(engine_with_mock_deps):
    manager = engine_with_mock_deps._db
    manager.store_memory(MemoryRecord(
        memory_id=_PARENT, profile_id="default", content="parent",
    ))
    return manager


def _store(db, content: str, *, profile_id: str = "default") -> str:
    fact = AtomicFact(
        memory_id=_PARENT, profile_id=profile_id, content=content,
        embedding=[0.03] * 768,
    )
    db.store_fact(fact)
    return fact.fact_id


def _both(db, ids, **kw) -> tuple[set[str], set[str]]:
    """(id-only answer, hydrating answer) for the same inputs."""
    fast = db.visible_fact_ids(list(ids), "default", **kw)
    slow = {f.fact_id for f in db.get_facts_by_ids(list(ids), "default", **kw)}
    return set(fast), slow


class TestTheTwoAnswersAgree:
    """The fast path is only safe while it is the same predicate."""

    def test_on_plain_visible_facts(self, db) -> None:
        ids = [_store(db, f"visible {i}") for i in range(5)]
        fast, slow = _both(db, ids)
        assert fast == slow == set(ids)

    def test_a_withheld_fact_is_refused_by_both(self, db) -> None:
        """Quarantine is enforced in one place; the fast path must honour it."""
        keep = _store(db, "ordinary memory")
        withheld = _store(db, "withheld pending review")
        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?", (withheld,),
        )

        fast, slow = _both(db, [keep, withheld])

        assert fast == slow == {keep}, (
            "the id-only path authorised a withheld memory the hydrating path "
            "refuses — a channel using it would surface a row nothing may show"
        )

    def test_include_quarantined_opens_both(self, db) -> None:
        """Repair and erasure must reach a withheld row through either path."""
        withheld = _store(db, "withheld but being repaired")
        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?", (withheld,),
        )

        fast, slow = _both(db, [withheld], include_quarantined=True)

        assert fast == slow == {withheld}

    def test_another_profiles_fact_is_refused_by_both(self, db) -> None:
        db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?,?)",
            ("other", "other"),
        )
        db.store_memory(MemoryRecord(
            memory_id="m-other", profile_id="other", content="parent",
        ))
        theirs = AtomicFact(
            memory_id="m-other", profile_id="other", content="not mine",
        )
        db.store_fact(theirs)
        mine = _store(db, "mine")

        fast, slow = _both(db, [mine, theirs.fact_id])

        assert fast == slow == {mine}

    def test_an_unknown_id_is_refused_by_both(self, db) -> None:
        mine = _store(db, "mine")
        fast, slow = _both(db, [mine, "does-not-exist"])
        assert fast == slow == {mine}

    def test_an_empty_request_asks_nothing(self, db) -> None:
        assert db.visible_fact_ids([], "default") == set()

    def test_a_batch_larger_than_one_chunk_is_complete(self, db) -> None:
        """The id list is unbounded; a single IN clause is not.

        1,700 ids crosses the internal batch size, so a batching bug shows up
        here as a short answer rather than as a SQL error at some later scale.
        """
        ids = [_store(db, f"bulk {i}") for i in range(1700)]
        fast = db.visible_fact_ids(ids, "default")
        assert fast == set(ids), f"expected {len(ids)}, got {len(fast)}"


class TestTheChannelBoundary:
    """``authorized_fact_ids`` is what the channels actually call."""

    def test_it_prefers_the_id_only_path(self, db) -> None:
        ids = [_store(db, f"candidate {i}") for i in range(4)]

        calls: list[int] = []
        original = db.get_facts_by_ids

        def _counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        db.get_facts_by_ids = _counted
        try:
            allowed = authorized_fact_ids(db, ids, "default")
        finally:
            db.get_facts_by_ids = original

        assert allowed == set(ids)
        assert not calls, (
            "authorisation hydrated the memories it was only asked to check"
        )

    def test_it_still_works_without_the_fast_path(self, db) -> None:
        """Lightweight DB wrappers in maintenance paths lack the method."""
        ids = [_store(db, f"candidate {i}") for i in range(4)]

        class _NoFastPath:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                if name == "visible_fact_ids":
                    raise AttributeError(name)
                return getattr(self._inner, name)

        assert authorized_fact_ids(_NoFastPath(db), ids, "default") == set(ids)
