# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Storing a memory record twice must not delete the facts extracted from it.

``store_memory`` used ``INSERT OR REPLACE``, which SQLite implements as a DELETE
followed by an INSERT. ``atomic_facts.memory_id`` is a foreign key with
``ON DELETE CASCADE``. So re-storing a record under an id that already existed
deleted every fact belonging to it, returned the memory_id as though it had
succeeded, and raised nothing.

It never fired in production because almost every caller passes a freshly
generated id — but ``cognitive_consolidator`` supplies its own ``block_id``, and
``run_store``'s queryable-promotion path avoids calling it for an existing
memory by convention rather than by construction. A silent delete of a user's
memories cannot be guarded by a convention.
"""

from __future__ import annotations

import pytest

from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.fixture
def db(engine_with_mock_deps):
    return engine_with_mock_deps._db


def _facts(db, memory_id: str) -> list[str]:
    return [
        r[0] for r in db.execute(
            "SELECT fact_id FROM atomic_facts WHERE memory_id = ?", (memory_id,),
        )
    ]


def test_restoring_the_same_memory_keeps_its_facts(db) -> None:
    """The defect, reproduced. Three facts in, three facts out."""
    db.store_memory(MemoryRecord(
        memory_id="m1", profile_id="default", content="the original turn",
    ))
    for i in range(3):
        db.store_fact(AtomicFact(
            memory_id="m1", profile_id="default", content=f"extracted fact {i}",
        ))
    assert len(_facts(db, "m1")) == 3

    db.store_memory(MemoryRecord(
        memory_id="m1", profile_id="default", content="the same turn, re-stored",
    ))

    assert len(_facts(db, "m1")) == 3, (
        "re-storing the parent memory deleted the facts extracted from it"
    )


def test_restoring_still_updates_the_record(db) -> None:
    """Last write wins is preserved — the fix must not turn it into a no-op."""
    db.store_memory(MemoryRecord(
        memory_id="m2", profile_id="default", content="first", speaker="a",
    ))
    db.store_memory(MemoryRecord(
        memory_id="m2", profile_id="default", content="second", speaker="b",
    ))

    row = dict(db.execute(
        "SELECT content, speaker FROM memories WHERE memory_id = ?", ("m2",),
    )[0])
    assert row["content"] == "second"
    assert row["speaker"] == "b"


def test_the_first_observation_time_is_not_rewritten(db) -> None:
    """When the memory was first seen is history; a re-store is not a new sighting."""
    db.store_memory(MemoryRecord(
        memory_id="m3", profile_id="default", content="first",
        created_at="2020-01-01T00:00:00+00:00",
    ))
    db.store_memory(MemoryRecord(
        memory_id="m3", profile_id="default", content="second",
        created_at="2026-08-22T00:00:00+00:00",
    ))

    created = dict(db.execute(
        "SELECT created_at FROM memories WHERE memory_id = ?", ("m3",),
    )[0])["created_at"]
    assert created == "2020-01-01T00:00:00+00:00"


def test_a_new_memory_id_still_inserts(db) -> None:
    """The ordinary path — the overwhelming majority of calls."""
    memory_id = db.store_memory(MemoryRecord(
        memory_id="m4", profile_id="default", content="brand new",
    ))
    assert memory_id == "m4"
    assert db.execute(
        "SELECT 1 FROM memories WHERE memory_id = ?", ("m4",),
    )
