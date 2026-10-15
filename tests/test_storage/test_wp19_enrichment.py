"""A legacy-shaped store converges without changing content or declared values."""

import json
import sqlite3

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.fixture
def legacy_store(tmp_path):
    from superlocalmemory.storage.migrations.M013_bi_temporal_columns import repair
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    with sqlite3.connect(db.db_path) as conn:
        repair(conn)
        apply(conn)
    for i in range(12):
        text = ('[] session ended 2026-04-01' if i == 11 else
                f'Zephyr is our project. Dr. Iyer uses PostgreSQL. #database Note {i}.')
        metadata = {'tags': ['owner'], 'project': 'OwnerProject'} if i == 0 else {}
        db.store_memory(MemoryRecord(memory_id=f'm{i}', content=text, metadata=metadata,
                                    created_at='2026-04-01T10:00:00+00:00'))
        db.store_fact(AtomicFact(fact_id=f'f{i}', memory_id=f'm{i}', content=text,
                                memory_kind='fact',
                                memory_kind_source='caller' if i == 0 else 'rules'))
    db.execute('UPDATE atomic_facts SET observation_date=NULL,valid_from=NULL,non_answer=0')
    db.execute('UPDATE fact_temporal_validity SET valid_from=NULL')
    yield db
    db.close()


def snapshot(db):
    return {table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in ('memories', 'atomic_facts', 'fact_temporal_validity',
                          'canonical_entities', 'fact_entity_associations')}


def test_coverage_rises_provenance_is_complete_and_undo_is_exact(legacy_store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    before = snapshot(db)
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(20):
        result = runner.run_batch(limit=3)
        if result['status'] == 'completed':
            break
    assert result['status'] == 'completed'
    assert result['processed'] == 12
    assert result['backup_point']
    rows = [dict(r) for r in db.execute('SELECT * FROM atomic_facts')]
    assert all(r['observation_date'] for r in rows)
    # Save time is not an event start: only a fact's own event date starts it.
    assert all(r['valid_from'] == r['referenced_date'] for r in rows)
    assert all(json.loads(r['canonical_entities_json']) for r in rows if not r['non_answer'])
    parents = [json.loads(r['metadata_json'])
               for r in db.execute('SELECT metadata_json FROM memories')]
    assert all(p.get('tags') and p.get('project') for p in parents[:-1])
    assert rows[0]['memory_kind_source'] == 'caller'
    assert parents[0] == {'tags': ['owner'], 'project': 'OwnerProject'}
    assert db.get_all_facts('default')
    changes = db.execute('SELECT * FROM enrichment_changes')
    assert changes
    assert all(r['enrichment_version'] and r['run_id'] for r in changes)
    # A date taken from created_at is labelled inferred; nothing else is.
    assert all((r['source'] == 'inferred') == (r['field'] == 'observation_date')
               for r in changes)
    runner.undo(result['run_id'])
    assert snapshot(db) == before


def test_enrichment_pauses_under_load_and_resumes_after_restart(legacy_store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    busy = True
    runner = EnrichmentRunner(db, idle=lambda: not busy)
    before = snapshot(db)
    assert runner.run_batch(limit=2)['status'] == 'waiting_for_idle'
    assert snapshot(db) == before
    busy = False
    result = runner.run_batch(limit=2)
    assert result['processed'] == 2
    runner.pause(result['run_id'])
    assert runner.run_batch(limit=2)['status'] == 'paused'
    runner = EnrichmentRunner(db, idle=lambda: True)
    runner.resume(result['run_id'])
    assert runner.run_batch(limit=2)['processed'] == 4


def test_undo_preserves_a_later_user_edit(legacy_store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    runner = EnrichmentRunner(db, idle=lambda: True)
    result = runner.run_batch(limit=12)
    db.execute("UPDATE memories SET metadata_json=? WHERE memory_id='m1'",
               (json.dumps({'tags': ['user'], 'project': 'UserProject'}),))
    runner.undo(result['run_id'])
    assert json.loads(db.execute("SELECT metadata_json FROM memories WHERE memory_id='m1'")
                      [0]['metadata_json']) == {'tags': ['user'], 'project': 'UserProject'}


def test_backup_failure_does_not_touch_facts_or_break_recall(legacy_store, monkeypatch):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    before = snapshot(db)
    runner = EnrichmentRunner(db, idle=lambda: True)
    monkeypatch.setattr(runner, '_backup', lambda: (_ for _ in ()).throw(OSError('disk full')))
    result = runner.run_batch(limit=12)
    assert result['errors'] > 0
    assert snapshot(db) == before
    assert db.get_all_facts('default')


def _finish(runner):
    for _ in range(20):
        result = runner.run_batch(limit=12)
        if result['status'] == 'completed':
            return result
    raise AssertionError('enrichment did not finish')


def test_legacy_fact_without_a_temporal_row_stays_legacy_unknown(legacy_store):
    """Review H2: absence of a temporal row means legacy_unknown; never backfilled."""
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    db.execute("DELETE FROM fact_temporal_validity WHERE fact_id IN ('f1','f2')")
    db.execute("UPDATE atomic_facts SET referenced_date='2026-03-02' WHERE fact_id='f3'")
    ids = [f'f{i}' for i in range(12)]

    def inadmissible():
        return db.get_strict_temporal_inadmissible_fact_ids(
            ids, 'default', known_as_of='2025-06-01', include_unknown=True)

    before = inadmissible()
    temporal_before = {r['fact_id']: dict(r) for r in
                       db.execute('SELECT * FROM fact_temporal_validity')}
    _finish(EnrichmentRunner(db, idle=lambda: True))
    assert inadmissible() == before
    temporal_after = {r['fact_id']: dict(r) for r in
                      db.execute('SELECT * FROM fact_temporal_validity')}
    assert set(temporal_after) == set(temporal_before)
    assert 'f1' not in temporal_after and 'f2' not in temporal_after
    # Existing rows keep their transaction time; only an empty event start is filled.
    for fact_id, row in temporal_after.items():
        assert row['system_created_at'] == temporal_before[fact_id]['system_created_at']
        # Only an event date starts a fact (declared, or the one its own text
        # names: f11 says 2026-04-01); save time never does.
        expected = {'f3': '2026-03-02', 'f11': '2026-04-01'}.get(fact_id)
        assert (row['valid_from'] or '')[:10] == (expected or '')
    starts = {r['fact_id']: r['valid_from'] for r in db.execute(
        'SELECT fact_id,valid_from FROM atomic_facts')}
    assert {k for k, v in starts.items() if v} == {'f3', 'f11'}


def test_enrichment_never_replaces_an_existing_event_start(legacy_store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = legacy_store
    db.execute("UPDATE fact_temporal_validity SET valid_from='2019-06-01' WHERE fact_id='f3'")
    _finish(EnrichmentRunner(db, idle=lambda: True))
    assert db.get_temporal_validity('f3', 'default')['valid_from'] == '2019-06-01'
