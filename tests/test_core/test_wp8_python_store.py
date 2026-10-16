"""A standalone API owns the work it queues."""
from superlocalmemory.core.engine import MemoryEngine


def test_python_store_requires_canonical_completion(monkeypatch):
    import superlocalmemory.core.engine_ingestion as ingestion
    engine = object.__new__(MemoryEngine)
    monkeypatch.setattr(engine, '_require_full', lambda *a: None)
    monkeypatch.setattr(engine, '_ensure_init', lambda: None)
    seen = {}
    def save(*a, **kw):
        seen.update(kw)
        return ['fact']
    monkeypatch.setattr(ingestion, 'canonical_store', save)
    assert engine.store('Keep the approved incident decision') == ['fact']
    assert seen['require_complete'] is True


def test_standalone_store_materializes_before_return(engine_with_mock_deps):
    engine = engine_with_mock_deps
    ids = engine.store('Mira approved the database recovery decision yesterday.')
    assert ids
    rows = engine._db.execute('SELECT state FROM ingestion_operations')
    assert rows and {dict(row)['state'] for row in rows} == {'complete'}
