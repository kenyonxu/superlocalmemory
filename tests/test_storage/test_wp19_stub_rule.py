"""The stub rule is narrow, and a user's "this is an answer" always sticks (review M1)."""

import sqlite3

import pytest

from superlocalmemory.encoding.non_answer import is_stub, mark, user_unmark
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.mark.parametrize('text', [
    'use port 8765', 'My wifi is Zephyr5G', 'Deploy at 5pm',
    'memory id: grandma-recipe', 'Fact ID: 2fa-backup-codes',
    'receipt: lunch with Priya', 'Session ID: abc123',
    'session ended', 'The session ended with a decision to use PostgreSQL.',
    'Run id: nightly-build-backup',
    # Short all-digit numbers are ordinary: receipts, dates, counters (NEW-4).
    'Receipt ID: 88291034', 'Memory: 20261012', 'Operation: 123456789',
    'Run ID: 12345678901', 'Fact id: 00000000',
])
def test_meaningful_short_text_is_never_a_stub(text):
    assert not is_stub(text)


@pytest.mark.parametrize('text', [
    'Session ID: 9f8e7d6c5b4a', 'session ended 2026-10-12 11:28',
    '[] session ended 2026-04-01', 'Receipt: AB12CD34EF',
    'operation_id=0123abcd4567ef89', 'memory id: 123e4567-e89b-12d3-a456-426614174000',
    'Run ID: 01ARZ3NDEKTSV4RRFFQ69G5FAV', 'session closed at 2026-10-12T11:28:00Z.',
    # 12 or more digits is a machine id; hex needs a letter a-f and 8+ characters.
    'Operation ID: 123456789012', 'Receipt: 20261012112800', 'Session: deadbeef',
])
def test_label_plus_machine_id_or_session_boilerplate_is_a_stub(text):
    assert is_stub(text)


@pytest.fixture
def db(tmp_path):
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    manager = DatabaseManager(tmp_path / 'memory.db')
    manager.initialize(schema)
    with sqlite3.connect(manager.db_path) as conn:
        apply(conn)
    yield manager
    manager.close()


def _flag(db, fact_id):
    return db.execute('SELECT non_answer FROM atomic_facts WHERE fact_id=?', (fact_id,))[0][0]


def test_a_user_unmarked_fact_is_never_flagged_again(db):
    text = 'Session ID: 9f8e7d6c5b4a'
    db.store_memory(MemoryRecord(memory_id='m1', content=text))
    fact = AtomicFact(fact_id='f1', memory_id='m1', content=text)
    db.store_fact(fact)
    assert _flag(db, 'f1') == 1
    user_unmark(db, 'f1', 'default')
    assert _flag(db, 'f1') == 0
    mark(db, 'f1', 'default', text)
    db.store_temporal_validity('f1', 'default', valid_from='2026-01-01', fill_only=True)
    db.store_fact(AtomicFact(memory_id='m1', content=text))  # a duplicate re-save
    assert _flag(db, 'f1') == 0
    assert [f.fact_id for f in db.get_all_facts('default')] == ['f1']


def test_enrichment_respects_the_user_unmark(db):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    text = 'Session ID: 9f8e7d6c5b4a'
    db.store_memory(MemoryRecord(memory_id='m1', content=text))
    db.store_fact(AtomicFact(fact_id='f1', memory_id='m1', content=text))
    user_unmark(db, 'f1', 'default')
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(5):
        if runner.run_batch(limit=10)['status'] == 'completed':
            break
    assert _flag(db, 'f1') == 0
