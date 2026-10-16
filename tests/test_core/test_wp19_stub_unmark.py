"""A user's accepted edit of a stub clears the stub flag for good (review M1, NEW-4)."""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import (
    M018_ingestion_operations,
    M032_write_coordinator_admission,
    M033_projection_transactions,
    M034_obligation_integrity,
    M042_correction_case_ledger,
)
from superlocalmemory.storage.migrations import M052_memory_kinds as m052
from superlocalmemory.storage.migrations import M060_memory_description as m059
from superlocalmemory.storage.models import AtomicFact, FactType, MemoryRecord

ACTOR = 'daemon-capability:' + 'a' * 64


@pytest.fixture()
def env(tmp_path):
    path = tmp_path / 'memory.db'
    conn = sqlite3.connect(path)
    for migration in (M018_ingestion_operations, M032_write_coordinator_admission,
                      M033_projection_transactions, M034_obligation_integrity):
        migration.apply(conn)
    conn.commit()
    conn.close()
    db = DatabaseManager(path)
    db.initialize(schema)
    with db.raw_connection() as raw:
        M042_correction_case_ledger.apply(raw)
        m052.apply(raw)
        m059.apply(raw)
    runtime = CanonicalRememberRuntime(db=db, profile_id='default',
                                       writer=lambda _r, _o: [],
                                       journal_path=tmp_path / 'journal.db')
    runtime.start()
    yield db, runtime
    runtime.stop()


def _flags(db, fact_id):
    row = db.execute('SELECT non_answer, non_answer_user FROM atomic_facts WHERE fact_id=?',
                     (fact_id,))[0]
    return row['non_answer'], row['non_answer_user']


def _stub_edit(env, key):
    db, runtime = env
    text = 'Session ID: 9f8e7d6c5b4a'
    memory_id = db.store_memory(MemoryRecord(profile_id='default', content=text))
    fact_id = db.store_fact(AtomicFact(profile_id='default', memory_id=memory_id,
                                       content=text, fact_type=FactType.SEMANTIC))
    assert _flags(db, fact_id)[0] == 1
    proposed = runtime.create_correction_successor(
        'default', fact_id, f'succ{key}', 'Session ID: 9f8e7d6c5b4b',
        trusted_actor_id=ACTOR, idempotency_key=f'edit-{key}')
    assert proposed['status'] == 'proposed'
    return fact_id, proposed


def _transition(runtime, proposed, action, key):
    return runtime.transition_correction(
        'default', proposed['case_id'], action=action, expected_version=proposed['version'],
        actor_id=ACTOR, idempotency_key=f'{action}-{key}')


def test_a_proposed_edit_does_not_clear_the_stub_flag(env):
    db, _runtime = env
    fact_id, proposed = _stub_edit(env, '0000000000a1')
    assert _flags(db, fact_id) == (1, None)
    assert _flags(db, proposed['successor_fact_id'])[1] is None


def test_applying_the_edit_unmarks_predecessor_and_successor(env):
    db, runtime = env
    fact_id, proposed = _stub_edit(env, '0000000000a2')
    applied = _transition(runtime, proposed, 'apply', 'a2')
    assert applied['status'] == 'applied'
    assert _flags(db, fact_id) == (0, 0)
    assert _flags(db, proposed['successor_fact_id']) == (0, 0)


def test_a_rejected_edit_leaves_the_original_flag_as_it_was(env):
    db, runtime = env
    fact_id, proposed = _stub_edit(env, '0000000000a3')
    rejected = _transition(runtime, proposed, 'reject', 'a3')
    assert rejected['status'] == 'rejected'
    assert _flags(db, fact_id) == (1, None)
