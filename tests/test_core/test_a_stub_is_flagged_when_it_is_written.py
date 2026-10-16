"""A stub is flagged by the save that writes it, not by a later enrichment pass.

Measured on one real store: only 30 of 291 bare "session ended" rows carried
non_answer=1. Recall cannot drop a row it was never told is a stub, and the
background pass that sets the flag had not reached most of them. This runs the
real queryable writer (the one every remember door reaches) with no enrichment
step at all, including the case that left rows unflagged: the same words
saved again onto a row an older version stored without the flag.
"""

from __future__ import annotations

import uuid

import pytest

from superlocalmemory.core.engine_ingestion import build_immediate_admission_handler
from superlocalmemory.core.ingestion_command import (
    IngestionCommand,
    IngestionOperationRepository,
    IngestionRequest,
)
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import M018_ingestion_operations, M060_memory_description

ACTOR = "daemon-capability:" + "9" * 64
STUBS = (
    "[Agentic_official] session ended 2026-10-13 11:01",
    "[Agentic_official] session ended 2026-10-13 09:54 | files: NOTICE, formats.md",
    "File changed: NOTICE",
    "[codex] session ended (exit) at 2026-10-13 11:01 in Agentic_official.",
)


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(tmp_path / "memory.db")
    manager.initialize(schema)
    with manager.raw_connection() as conn:
        M018_ingestion_operations.apply(conn)
        M060_memory_description.apply(conn)
    return manager


def _save(db, content: str) -> list[str]:
    command = IngestionCommand(
        IngestionOperationRepository(db),
        write_queryable=build_immediate_admission_handler(db, profile_id="default"),
        materialize=lambda *_: [],
    )
    receipt = command.submit(IngestionRequest(
        content=content, profile_id="default", source_type="http",
        idempotency_key=uuid.uuid4().hex, trusted_actor_id=ACTOR,
    ))
    return list(receipt.fact_ids)


def _flag(db, fact_id):
    row = db.execute("SELECT non_answer, non_answer_user FROM atomic_facts WHERE fact_id=?",
                     (fact_id,))[0]
    return row["non_answer"], row["non_answer_user"]


@pytest.mark.parametrize("text", STUBS)
def test_a_hook_line_is_flagged_by_the_save_itself(db, text):
    (fact_id,) = _save(db, text)
    assert _flag(db, fact_id) == (1, None)


def test_a_real_memory_is_not_flagged(db):
    (fact_id,) = _save(db, "Migrating the search index to the new embedding model.")
    assert _flag(db, fact_id) == (0, None)


def test_saving_a_stub_again_flags_the_row_an_older_version_left_unflagged(db):
    text = STUBS[0]
    (fact_id,) = _save(db, text)
    db.execute("UPDATE atomic_facts SET non_answer=0 WHERE fact_id=?", (fact_id,))
    assert _flag(db, fact_id) == (0, None)
    assert _save(db, text) == [fact_id]  # the same words fold onto the same row
    assert _flag(db, fact_id) == (1, None)


def test_the_users_own_decision_still_wins_over_the_save(db):
    text = STUBS[2]
    (fact_id,) = _save(db, text)
    db.execute("UPDATE atomic_facts SET non_answer=0, non_answer_user=0 WHERE fact_id=?",
               (fact_id,))
    assert _save(db, text) == [fact_id]
    assert _flag(db, fact_id) == (0, 0)
