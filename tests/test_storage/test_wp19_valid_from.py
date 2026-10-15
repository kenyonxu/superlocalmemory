"""Every canonical fact has event time independent of knowledge time."""

import sqlite3

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.mark.parametrize(
    ('event', 'observed', 'expected'),
    [('2026-03-02', '2026-04-01', '2026-03-02'),
     (None, '2026-04-01', '2026-04-01'),
     # Save time is transaction time, never an event start: unknown stays unknown,
     # so a fact is not "not yet valid" for an as_of before it was saved.
     (None, None, None)],
)
@pytest.mark.parametrize('writer', ['store_fact', 'insert_fact_immutable'])
def test_every_save_writes_the_time_the_fact_became_true(
    tmp_path, event, observed, expected, writer,
):
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        from superlocalmemory.storage.migrations.M013_bi_temporal_columns import repair
        with sqlite3.connect(db.db_path) as conn:
            repair(conn)
        db.store_memory(MemoryRecord(memory_id='source', content='Zephyr started.'))
        fact = AtomicFact(memory_id='source', content='Zephyr started.',
                          referenced_date=event, observation_date=observed,
                          created_at='2026-04-01T12:00:00+00:00')
        getattr(db, writer)(fact)
        validity = db.get_temporal_validity(fact.fact_id, 'default')
        assert validity['valid_from'] == expected
        assert validity['system_created_at']
        assert db.execute('SELECT valid_from FROM atomic_facts WHERE fact_id=?',
                          (fact.fact_id,))[0]['valid_from'] == expected
    finally:
        db.close()


def test_retry_repairs_anchor_using_retained_event_time(tmp_path):
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        db.store_memory(MemoryRecord(memory_id='source', content='Zephyr started.'))
        fact = AtomicFact(memory_id='source', content='Zephyr started.',
                          referenced_date='2026-03-02')
        db.store_fact(fact)
        db.execute('DELETE FROM fact_temporal_validity WHERE fact_id=?', (fact.fact_id,))
        duplicate = AtomicFact(memory_id='source', content=fact.content)
        assert db.store_fact(duplicate) == fact.fact_id
        assert db.get_temporal_validity(fact.fact_id)['valid_from'] == '2026-03-02'
    finally:
        db.close()
