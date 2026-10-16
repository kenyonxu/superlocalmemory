"""Undo after a profile was deleted (folded) restores exactly (review M3).

Repro: an improvement run is interrupted at 6 of 10 facts of profile 'work',
'work' is deleted (its memories fold into 'default', whose same-named
entities absorb the ones the run created), then the run is undone. Every
value the run added must be gone and nothing may count as a user edit.
"""

import sqlite3

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, CanonicalEntity, MemoryRecord

TABLES = ('memories', 'atomic_facts', 'canonical_entities', 'fact_entity_associations',
          'fact_temporal_validity')


def _store(path):
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(path)
    db.initialize(schema)
    with sqlite3.connect(db.db_path) as conn:
        apply(conn)
    db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('work','work')")
    for name in ('Zephyr', 'PostgreSQL'):
        db.store_entity(CanonicalEntity(entity_id=f'default-{name}', profile_id='default',
                                        canonical_name=name))
    for i in range(10):
        text = f'Zephyr moved the billing jobs to PostgreSQL. Note {i}.'
        db.store_memory(MemoryRecord(memory_id=f'm{i}', profile_id='work', content=text,
                                     created_at='2026-04-01T10:00:00+00:00'))
        db.store_fact(AtomicFact(fact_id=f'f{i}', memory_id=f'm{i}', profile_id='work',
                                 content=text, created_at='2026-04-01T10:00:00+00:00'))
    db.execute("UPDATE atomic_facts SET entities_json='[]', canonical_entities_json='[]'")
    db.execute('DELETE FROM fact_entity_associations')
    return db


def _fold(db):
    from superlocalmemory.storage.profile_fold import fold_profile
    conn = sqlite3.connect(db.db_path, isolation_level=None)
    try:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        except (ImportError, AttributeError, sqlite3.OperationalError):
            pass
        conn.execute('BEGIN IMMEDIATE')
        fold_profile(conn, 'work', 'default')
        conn.execute("DELETE FROM profiles WHERE profile_id='work'")
        conn.execute('COMMIT')
    finally:
        conn.close()


def _snapshot(db):
    out = {}
    for table in TABLES:
        rows = [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
        for row in rows:
            for volatile in ('system_created_at', 'last_seen', 'first_seen', 'created_at'):
                row.pop(volatile, None)
        out[table] = sorted(rows, key=lambda r: sorted(r.items()).__repr__())
    return out


def test_undo_after_the_profile_folded_leaves_nothing_behind(tmp_path):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    baseline = _store(tmp_path / 'baseline' / 'memory.db')
    improved = _store(tmp_path / 'improved' / 'memory.db')
    try:
        runner = EnrichmentRunner(improved, idle=lambda: True)
        run = runner.run_batch(limit=6)
        assert run['processed'] == 6 and run['status'] == 'running'
        added = improved.execute("SELECT COUNT(*) AS n FROM canonical_entities "
                                 "WHERE profile_id='work'")[0]['n']
        assert added, 'the run created no entities, so the fold merges nothing'
        _fold(improved)
        _fold(baseline)
        result = runner.undo(run['run_id'])
        assert result['conflicts'] == 0, result
        assert _snapshot(improved) == _snapshot(baseline)
    finally:
        baseline.close()
        improved.close()
