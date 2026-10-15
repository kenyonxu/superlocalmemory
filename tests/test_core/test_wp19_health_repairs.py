"""Automatic safe repairs: dry run first, one safety copy per store, exact undo."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.fixture
def store(tmp_path):
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    with sqlite3.connect(db.db_path) as conn:
        apply(conn)
        conn.execute(
            'CREATE TABLE IF NOT EXISTS fact_retention (fact_id TEXT PRIMARY KEY, '
            'profile_id TEXT NOT NULL, retention_score REAL NOT NULL DEFAULT 1.0, '
            'memory_strength REAL NOT NULL DEFAULT 1.0, access_count INTEGER NOT NULL DEFAULT 0, '
            'last_accessed_at TEXT, last_computed_at TEXT NOT NULL DEFAULT (datetime(\'now\')), '
            "lifecycle_zone TEXT NOT NULL DEFAULT 'active')")
    for i in range(3):
        text = f'Zephyr uses PostgreSQL for record {i}.'
        db.store_memory(MemoryRecord(memory_id=f'm{i}', content=text))
        db.store_fact(AtomicFact(fact_id=f'f{i}', memory_id=f'm{i}', content=text))
    # f0 and f1 are hidden although their score says they are in use.
    for fid, zone, score in (('f0', 'archive', 0.95), ('f1', 'forgotten', 0.9),
                             ('f2', 'archive', 0.1)):
        db.execute("INSERT OR REPLACE INTO fact_retention(fact_id,profile_id,retention_score,"
                   "lifecycle_zone,last_computed_at) VALUES (?,?,?,?,'2026-04-01')",
                   (fid, 'default', score, zone))
        db.execute("UPDATE atomic_facts SET lifecycle='archived' WHERE fact_id=?", (fid,))
    yield SimpleNamespace(_db=db)
    db.close()


def snapshot(db):
    return {t: [dict(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')]
            for t in ('atomic_facts', 'fact_retention')}


def test_refile_hidden_dry_run_changes_nothing_then_apply_is_undoable(store):
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    db = store._db
    before = snapshot(db)
    planned = repair_action(store, 'refile-hidden', dry_run=True)
    assert planned['found'] == 2 and planned['fixed'] == 0
    assert snapshot(db) == before
    applied = repair_action(store, 'refile-hidden', dry_run=False)
    assert applied['fixed'] == 2
    zones = {r['fact_id']: r['lifecycle_zone'] for r in db.execute('SELECT * FROM fact_retention')}
    assert zones == {'f0': 'active', 'f1': 'active', 'f2': 'archive'}
    row = db.execute('SELECT * FROM memory_health_repairs WHERE run_id=?', (applied['run_id'],))[0]
    assert (row['action'], row['found'], row['fixed']) == ('refile-hidden', 2, 2)
    undo_action(store, applied['run_id'])
    assert snapshot(db) == before
    assert db.execute('SELECT status FROM memory_health_repairs WHERE run_id=?',
                      (applied['run_id'],))[0]['status'] == 'undone'


def _tick(engine, idle=lambda: True):
    from superlocalmemory.core.health_repairs import run_safe_repairs
    engine._memory_health_checked = None
    run_safe_repairs(engine, idle=idle)


def test_idle_worker_never_moves_memories_it_only_suggests(store):
    """Review H1: archive fact with retention 0.95 -> no automatic change."""
    db = store._db
    before = snapshot(db)
    _tick(store, idle=lambda: False)
    assert db.execute('SELECT COUNT(*) AS n FROM memory_health_repairs')[0]['n'] == 0
    _tick(store)
    assert snapshot(db) == before
    rows = [dict(r) for r in db.execute('SELECT * FROM memory_health_repairs')]
    assert [(r['action'], r['status'], r['found'], r['fixed']) for r in rows
            if r['action'] == 'refile-hidden'] == [('refile-hidden', 'suggested', 2, 0)]
    assert not any(r['status'] == 'completed' for r in rows)
    # Nothing changed, so no safety copy was taken either.
    assert not list((Path(db.db_path).parent / 'pre-migration-snapshots').glob('*.db'))


def test_a_manual_fix_that_was_undone_stays_undone(store):
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    db = store._db
    before = snapshot(db)
    applied = repair_action(store, 'refile-hidden', dry_run=False)
    assert applied['fixed'] == 2
    undo_action(store, applied['run_id'])
    assert snapshot(db) == before
    for _ in range(3):
        _tick(store)
    assert snapshot(db) == before
    assert db.execute('SELECT status FROM memory_health_repairs WHERE run_id=?',
                      (applied['run_id'],))[0]['status'] == 'undone'


def test_only_add_only_repairs_run_automatically(store, monkeypatch):
    from superlocalmemory.core import health_repairs
    calls = []

    def fake(action):
        def run(engine, dry_run, idle, skip=frozenset()):
            calls.append((action, dry_run))
            return {'found': 3, 'fixed': 0 if dry_run else 3}
        return run
    monkeypatch.setattr(health_repairs, '_RUNNERS',
                        {a: fake(a) for a in health_repairs.ACTIONS})
    _tick(store)
    applied = {a for a, dry in calls if not dry}
    assert applied == set(health_repairs.AUTO_ACTIONS) == {'qjl-sketches'}
    assert {'entity-links', 'refile-hidden'} <= {a for a, dry in calls if dry}


def test_the_manual_fix_takes_one_safety_copy_that_enrichment_reuses(store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    from superlocalmemory.core.health_repairs import repair_action
    db = store._db
    repair_action(store, 'refile-hidden', dry_run=False)
    snapshots = Path(db.db_path).parent / 'pre-migration-snapshots'
    copies = sorted(snapshots.glob('*-pre-migration.db'))
    assert len(copies) == 1
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(5):
        if runner.run_batch(limit=10)['status'] == 'completed':
            break
    assert sorted(snapshots.glob('*-pre-migration.db')) == copies


def test_repeated_failures_are_one_row_with_a_count_and_back_off(store, monkeypatch):
    """Review L2."""
    from superlocalmemory.core import health_repairs
    calls = []

    def broken(engine, dry_run, idle, skip=frozenset()):
        calls.append(dry_run)
        raise RuntimeError('private memory text must not be stored')
    runners = dict(health_repairs._RUNNERS)
    runners['qjl-sketches'] = broken
    monkeypatch.setattr(health_repairs, '_RUNNERS', runners)
    db = store._db

    def errors():
        return [dict(r) for r in db.execute(
            "SELECT * FROM memory_health_repairs WHERE status='error'")]
    _tick(store)
    assert len(calls) == 1
    first = errors()
    assert len(first) == 1 and first[0]['attempts'] == 1
    assert first[0]['error'] == 'RuntimeError'  # never the message
    _tick(store)  # inside the back-off window: not tried again
    assert len(calls) == 1 and errors()[0]['attempts'] == 1
    db.execute("UPDATE memory_health_repairs SET last_seen='2020-01-01T00:00:00+00:00' "
               "WHERE status='error'")
    _tick(store)
    assert len(calls) == 2
    again = errors()
    assert len(again) == 1 and again[0]['attempts'] == 2


def test_unknown_repair_is_refused(store):
    from superlocalmemory.core.health_repairs import repair_action
    with pytest.raises(ValueError):
        repair_action(store, 'drop-everything')


def test_a_finished_pass_is_followed_only_by_new_facts(store):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    db = store._db
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(5):
        done = runner.run_batch(limit=10)
        if done['status'] == 'completed':
            break
    assert done['processed'] == 3
    # A day later only what was saved since is looked at again.
    db.execute("UPDATE enrichment_runs SET updated_at='2026-01-01T00:00:00+00:00'")
    db.store_memory(MemoryRecord(memory_id='m9', content='Atlas is our project.'))
    db.store_fact(AtomicFact(fact_id='f9', memory_id='m9', content='Atlas is our project.'))
    nxt = runner.run_batch(limit=10)
    assert nxt['run_id'] != done['run_id']
    assert (nxt['total'], nxt['processed']) == (1, 1)


def test_an_undone_automatic_sketch_repair_is_never_reapplied(tmp_path):
    """Review H1: auto repair -> undo -> the next idle ticks leave it undone."""
    from dataclasses import replace

    import numpy as np

    from superlocalmemory.core.config import PolarQuantConfig, SLMConfig
    from superlocalmemory.core.engine_wiring import init_retrieval
    from superlocalmemory.core.health_repairs import undo_action
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    from superlocalmemory.storage.models import Mode

    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.embedding = replace(config.embedding, dimension=16)
    config.quantization = replace(config.quantization, polar=PolarQuantConfig(
        dimension=16, rotation_matrix_path=str(tmp_path / 'rotation.npy')))
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    with sqlite3.connect(db.db_path) as conn:
        apply(conn)
    retrieval = init_retrieval(config, db, None, None, None, vector_store=None)
    try:
        qstore = retrieval._semantic._qas._quantized_store
        vector = np.random.default_rng(40).standard_normal(16)
        db.store_memory(MemoryRecord(memory_id='m1', content='Legacy sketch'))
        db.store_fact(AtomicFact(fact_id='f1', memory_id='m1', content='Legacy sketch',
                                 embedding=vector.tolist()))
        assert qstore.compress_fact('f1', 'default', vector, 4)
        legacy = qstore.load('f1', 'default').qjl_bits[12:]
        db.execute('UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id=?', (legacy, 'f1'))
        engine = SimpleNamespace(_db=db, _config=config)
        _tick(engine)
        repaired = db.execute("SELECT run_id FROM memory_health_repairs "
                              "WHERE action='qjl-sketches' AND status='completed'")
        assert repaired and qstore.load('f1', 'default').qjl_bits.startswith(b'QJL1')
        undo_action(engine, repaired[0]['run_id'])
        assert qstore.load('f1', 'default').qjl_bits == legacy
        for _ in range(3):
            _tick(engine)
        assert qstore.load('f1', 'default').qjl_bits == legacy
    finally:
        retrieval.close(wait=True)
        db.close()


def test_first_idle_check_runs_on_a_host_that_booted_minutes_ago(store, monkeypatch):
    """time.monotonic() counts from boot on Linux, so a fresh CI runner or a
    just-restarted server reports a few minutes. The first check must still run:
    "never checked" is not the same as "checked at second zero"."""
    from superlocalmemory.core import health_repairs
    monkeypatch.setattr(health_repairs.time, 'monotonic', lambda: 90.0)
    health_repairs.run_safe_repairs(store, idle=lambda: True)
    rows = [dict(r) for r in store._db.execute('SELECT * FROM memory_health_repairs')]
    assert any(r['action'] == 'refile-hidden' and r['status'] == 'suggested' for r in rows)
    # And the interval still throttles the next call.
    store._db.execute('DELETE FROM memory_health_repairs')
    health_repairs.run_safe_repairs(store, idle=lambda: True)
    assert store._db.execute('SELECT COUNT(*) AS n FROM memory_health_repairs')[0]['n'] == 0
