"""Every public save surface lands on canonical admission and description."""

import pytest

from superlocalmemory.core.engine_ingestion import build_immediate_admission_handler
from superlocalmemory.core.ingestion_command import IngestionRequest
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager

# Deliberately enumerate the product surfaces: adding one requires extending
# this contract, rather than copying an incomplete description path.
SAVE_PATHS = (
    ('MCP remember', 'mcp-offline-worker'),
    ('CLI remember', 'http'),
    ('hooks', 'http-observe'),
    ('web connector', 'http'),
    ('document ingestion', 'document'),
    ('picture ingestion', 'media'),
    ('Python prebuilt fact', 'python-api-prebuilt'),
)


@pytest.mark.parametrize('surface,source_type', SAVE_PATHS)
def test_surface_has_immediate_description_and_durable_event_time(tmp_path, surface, source_type):
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        writer = build_immediate_admission_handler(db, profile_id='default')
        request = IngestionRequest(
            idempotency_key='synthetic-key', profile_id='default', source_type=source_type,
            trusted_actor_id='synthetic-actor',
            content='Zephyr is our project. PostgreSQL stores the records. #database',
            session_date='2026-04-01',
        )
        with db.transaction():
            ids = writer(request, 'synthetic-operation')
        assert ids, surface
        fact = db.get_fact(ids[0])
        assert 'Zephyr' in fact.entities
        assert fact.observation_date == '2026-04-01'
        assert db.get_temporal_validity(ids[0])['valid_from'] == '2026-04-01'
        import json
        parent = json.loads(db.execute('SELECT metadata_json FROM memories WHERE memory_id=?',
                                       (fact.memory_id,))[0]['metadata_json'])
        assert parent['project'] == 'Zephyr'
        assert parent['tags'] == ['database']
    finally:
        db.close()


def test_document_and_picture_saves_reach_the_same_runtime_writer():
    """The two ingestion pipelines submit through the remember runtime, whose
    every admission writer is the canonical description handler above."""
    import inspect

    import superlocalmemory.core.remember_runtime as runtime
    import superlocalmemory.documents.pipeline as documents
    import superlocalmemory.media.ingest as media
    for module, source_type in ((documents, 'document'), (media, 'media')):
        text = inspect.getsource(module)
        assert 'submit_memory_settled(' in text and f'source_type="{source_type}"' in text
    writers = inspect.getsource(runtime)
    assert writers.count('writer=build_immediate_admission_handler(') + \
        writers.count('writer = build_immediate_admission_handler(') >= 3
