"""Operational receipts are durable but cannot become answers."""

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.mark.parametrize('text', ['[] session ended 2026-04-01', '[]',
                                  'receipt: 0123abcd4567ef89'])
def test_stubs_are_listed_with_a_flag_but_never_hydrated_for_recall(tmp_path, text):
    """Review H3: list/count/search show stubs (flagged); recall opts out of them."""
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            apply(conn)
        db.store_memory(MemoryRecord(memory_id='source', content=text))
        fact = AtomicFact(memory_id='source', content=text)
        db.store_fact(fact)
        assert db.get_fact(fact.fact_id) is not None
        listed = db.get_all_facts('default')
        assert [f.fact_id for f in listed] == [fact.fact_id]
        assert listed[0].non_answer is True
        assert db.get_fact_count('default') == 1
        assert db.get_facts_by_ids([fact.fact_id], 'default')[0].non_answer is True
        # The recall answer path opts in to the exclusion.
        assert db.get_facts_by_ids([fact.fact_id], 'default', exclude_non_answer=True) == []
        assert db.visible_fact_ids([fact.fact_id], 'default', exclude_non_answer=True) == set()
        assert db.visible_fact_ids([fact.fact_id], 'default') == {fact.fact_id}
    finally:
        db.close()


def test_list_recent_includes_stubs(tmp_path):
    from superlocalmemory.core.kind_query import list_recent_facts
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            apply(conn)
        for i, text in enumerate(('Session ID: 9f8e7d6c5b4a', 'Zephyr uses PostgreSQL.')):
            db.store_memory(MemoryRecord(memory_id=f'm{i}', content=text))
            db.store_fact(AtomicFact(fact_id=f'f{i}', memory_id=f'm{i}', content=text))
        facts = {f.fact_id: f.non_answer for f in list_recent_facts(db, 'default', 10, None)}
        assert facts == {'f0': True, 'f1': False}
    finally:
        db.close()


def test_short_meaningful_facts_remain_answer_material(tmp_path):
    from superlocalmemory.encoding.non_answer import is_stub
    assert not is_stub('I prefer tea.')
    assert not is_stub('Zephyr uses PostgreSQL.')
    assert not is_stub('The session ended with a decision to use PostgreSQL.')


@pytest.mark.parametrize('text', ['[]', '{}', '[ ]', '()', '[{}]', ' {} ', '[],[]'])
def test_empty_structures_are_rejected_at_admission(text):
    """Review L3: empty-structure content never reaches durable ingestion."""
    from superlocalmemory.core.engine_ingestion import content_passes_admission
    assert content_passes_admission(text) is False


def test_session_boilerplate_is_still_admitted_as_a_durable_stub():
    from superlocalmemory.core.engine_ingestion import content_passes_admission
    assert content_passes_admission('session ended 2026-10-12 11:28') is True
