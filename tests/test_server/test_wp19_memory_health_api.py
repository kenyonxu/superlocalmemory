"""Shared health routes require owner permission and durable undo identities."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import memory_health


def client(monkeypatch):
    app = FastAPI()
    app.include_router(memory_health.router)
    app.state.engine = SimpleNamespace(_db=Mock())
    monkeypatch.setattr(memory_health, 'report', lambda db: {'enrichment': {'status': 'running'}})
    return TestClient(app)


def test_health_read_obeys_manage_permission(monkeypatch):
    def refuse(*args, **kwargs):
        raise HTTPException(403, 'Owner permission required')
    monkeypatch.setattr(memory_health, 'require_permission', refuse)
    with client(monkeypatch) as session:
        assert session.get('/api/memory-health').status_code == 403


def test_cli_and_dashboard_share_pause_action(monkeypatch):
    monkeypatch.setattr(memory_health, 'require_permission', lambda *a, **k: None)
    monkeypatch.setattr(memory_health.write_identity, 'require_write_actor',
                        lambda *a, **k: 'owner')
    paused = Mock(return_value={'status': 'paused'})
    monkeypatch.setattr(memory_health.EnrichmentRunner, 'pause', paused)
    with client(monkeypatch) as session:
        response = session.post('/api/memory-health/action',
                                json={'action': 'pause', 'run_id': 'synthetic-run'})
        assert response.status_code == 200
        assert response.json()['status'] == 'paused'
        paused.assert_called_once_with('synthetic-run')


def test_undoing_twice_answers_422_with_a_plain_reason(monkeypatch, tmp_path):
    from superlocalmemory.core.health_repairs import repair_action
    from tests.test_core.test_wp19_health_repairs import store as make_store

    monkeypatch.setattr(memory_health, 'require_permission', lambda *a, **k: None)
    monkeypatch.setattr(memory_health.write_identity, 'require_write_actor',
                        lambda *a, **k: 'owner')
    fixture = make_store.__wrapped__(tmp_path)
    engine = next(fixture)
    try:
        run_id = repair_action(engine, 'refile-hidden', dry_run=False)['run_id']
        app = FastAPI()
        app.include_router(memory_health.router)
        app.state.engine = engine
        with TestClient(app) as session:
            body = {'action': 'undo', 'run_id': run_id}
            assert session.post('/api/memory-health/action', json=body).status_code == 200
            again = session.post('/api/memory-health/action', json=body)
            assert again.status_code == 422
            assert again.json()['detail'] == 'That run was already undone.'
            listed = session.get('/api/memory-health').json()
            assert listed['runs'][0]['run_id'] == run_id
    finally:
        fixture.close()


def test_entity_links_fix_undo_and_include_undone_over_http(monkeypatch, tmp_path):
    import sqlite3

    from superlocalmemory.storage.migrations.M060_memory_description import apply
    from tests.test_storage.test_entity_link_repair import _build

    monkeypatch.setattr(memory_health, 'require_permission', lambda *a, **k: None)
    monkeypatch.setattr(memory_health.write_identity, 'require_write_actor',
                        lambda *a, **k: 'owner')
    db = _build(tmp_path / 'memory.db')
    with sqlite3.connect(db.db_path) as conn:
        apply(conn)
    try:
        app = FastAPI()
        app.include_router(memory_health.router)
        app.state.engine = SimpleNamespace(_db=db)
        with TestClient(app) as session:
            def post(body):
                return session.post('/api/memory-health/action', json=body)
            fixed = post({'action': 'repair', 'repair': 'entity-links'}).json()
            assert fixed['fixed'] == 27
            undone = post({'action': 'undo', 'run_id': fixed['run_id']}).json()
            assert undone['restored'] == 27 and undone['conflicts'] == 0
            assert post({'action': 'repair', 'repair': 'entity-links'}).json()['fixed'] == 0
            again = post({'action': 'repair', 'repair': 'entity-links',
                          'include_undone': True}).json()
            assert again['fixed'] == 27
    finally:
        db.close()
