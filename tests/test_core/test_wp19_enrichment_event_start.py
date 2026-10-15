"""Enrichment never turns save time into an event start (WP-19 re-check NEW-1).

An undated legacy fact may have its observation date filled from created_at (the
time it was recorded), but that filled value is inferred: it is labelled so in the
ledger and never becomes ``valid_from``. As-of recall must be exactly what it was
before enrichment ran.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, FactType, MemoryRecord


@pytest.fixture
def db(tmp_path):
    from superlocalmemory.storage.migrations.M013_bi_temporal_columns import repair
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    manager = DatabaseManager(tmp_path / 'memory.db')
    manager.initialize(schema)
    with sqlite3.connect(manager.db_path) as conn:
        repair(conn)
        apply(conn)
    yield manager
    manager.close()


def _enrich(db):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(20):
        if runner.run_batch(limit=25)['status'] == 'completed':
            return
    raise AssertionError('enrichment did not finish')


def _seed(db, *fact_ids, **fields):
    db.store_memory(MemoryRecord(memory_id='m0', content='parent'))
    for fid in fact_ids:
        db.store_fact(AtomicFact(fact_id=fid, memory_id='m0', content=f'fact {fid}',
                                 fact_type=FactType.SEMANTIC, **fields.get(fid, {})))
        db.store_temporal_validity(fid, 'default')


def _filter(db, as_of):
    from superlocalmemory.retrieval.temporal_validity_filter import TemporalValidityFilter
    results = {'semantic': [('F3', 0.8), ('F_valid', 0.9)]}
    out = TemporalValidityFilter(db, demotion_factor=0.25).filter(
        results, 'default', {'as_of': as_of})
    return dict(out['semantic'])


def _legacy_store(db):
    _seed(db, 'F3', 'F_valid')
    db.execute("UPDATE fact_temporal_validity SET system_expired_at='2026-01-01T00:00:00+00:00' "
               "WHERE fact_id='F3'")
    db.execute("UPDATE fact_temporal_validity SET valid_until='2025-06-01T00:00:00+00:00' "
               "WHERE fact_id='F_valid'")
    for fid in ('F3', 'F_valid'):
        db.execute("UPDATE atomic_facts SET observation_date=NULL, referenced_date=NULL, "
                   "valid_from=NULL WHERE fact_id=?", (fid,))


def test_as_of_recall_is_the_same_before_and_after_enrichment(db):
    _legacy_store(db)
    ids = ['F3', 'F_valid']
    before = (_filter(db, '2024-01-01T00:00:00+00:00'),
              db.get_event_time_expired_fact_ids(ids, 'default', as_of='2026-03-01T00:00:00+00:00'),
              db.get_event_time_expired_fact_ids(ids, 'default', as_of='2024-01-01T00:00:00+00:00'))
    _enrich(db)
    after = (_filter(db, '2024-01-01T00:00:00+00:00'),
             db.get_event_time_expired_fact_ids(ids, 'default', as_of='2026-03-01T00:00:00+00:00'),
             db.get_event_time_expired_fact_ids(ids, 'default', as_of='2024-01-01T00:00:00+00:00'))
    assert after == before
    assert after[0]['F3'] == pytest.approx(0.8)


def test_a_filled_observation_date_is_inferred_and_never_a_start(db):
    _legacy_store(db)
    _enrich(db)
    row = dict(db.execute("SELECT * FROM atomic_facts WHERE fact_id='F3'")[0])
    assert row['observation_date'], 'enrichment may record when the memory was saved'
    assert row['valid_from'] is None
    assert db.execute("SELECT valid_from FROM fact_temporal_validity WHERE fact_id='F3'"
                      )[0]['valid_from'] is None
    changes = [dict(r) for r in db.execute(
        "SELECT * FROM enrichment_changes WHERE target_id='F3' AND field='observation_date'")]
    assert changes and {c['source'] for c in changes} == {'inferred'}
    assert not db.execute("SELECT 1 FROM enrichment_changes WHERE field='valid_from'")


def test_a_fact_keeps_its_own_dates_as_the_start(db):
    _seed(db, 'F3', 'F_valid')
    db.execute("UPDATE atomic_facts SET observation_date='2025-02-03', referenced_date=NULL, "
               "valid_from=NULL WHERE fact_id='F3'")
    db.execute("UPDATE atomic_facts SET referenced_date='2024-11-05', observation_date=NULL, "
               "valid_from=NULL WHERE fact_id='F_valid'")
    _enrich(db)
    starts = {r['fact_id']: (r['valid_from'], r['vt']) for r in db.execute(
        'SELECT f.fact_id AS fact_id, f.valid_from AS valid_from, t.valid_from AS vt '
        'FROM atomic_facts f JOIN fact_temporal_validity t ON t.fact_id=f.fact_id')}
    assert starts['F3'] == ('2025-02-03', '2025-02-03')
    assert starts['F_valid'] == ('2024-11-05', '2024-11-05')
    observed = db.execute("SELECT source FROM enrichment_changes WHERE target_id='F3' "
                          "AND field='observation_date'")
    assert not observed  # the fact's own observation date was never filled


def test_undoing_a_filled_observation_date_restores_it(db):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    _legacy_store(db)
    _enrich(db)
    run_id = db.execute("SELECT run_id FROM enrichment_runs WHERE action='enrichment'")[0]['run_id']
    EnrichmentRunner(db, idle=lambda: False).undo(run_id)
    assert db.execute("SELECT observation_date FROM atomic_facts WHERE fact_id='F3'"
                      )[0]['observation_date'] in (None, '')
