"""The dashboard list and its count include stubs, flagged (review H3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_server.test_a_listed_memory_belongs_to_its_profile import _daemon_headers

_ROWS = (('keep-1', 'Varun decided the release ships once both audits are clean.', 0),
         ('stub-1', 'Session ID: 9f8e7d6c5b4a', 1))


@pytest.fixture()
def client(engine_with_mock_deps):
    from superlocalmemory.server.profile_runtime import bind_profile_runtime
    from superlocalmemory.server.unified_daemon import create_app

    engine = engine_with_mock_deps
    engine.profile_id = 'default'
    engine._config.active_profile = 'default'
    engine._db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) "
                       "VALUES ('default','default')")
    engine._db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, session_id, speaker, role, "
        "created_at, metadata_json, scope) VALUES ('m1','default','source','s1','user',"
        "'user','2026-01-01T00:00:00Z','{}','personal')")
    for fid, content, stub in _ROWS:
        engine._db.execute(
            'INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, lifecycle, '
            'created_at, scope, non_answer) VALUES (?,?,?,?,?,?,?,?)',
            (fid, 'm1', 'default', content, 'active', '2026-01-01T00:00:00Z', 'personal', stub))
    app = create_app()
    app.state.engine = engine
    app.state.config = engine._config
    bind_profile_runtime(app.state, engine, engine._config)
    yield TestClient(app), _daemon_headers(app)


def test_the_list_and_total_include_a_flagged_stub(client) -> None:
    tc, headers = client
    body = tc.get('/api/memories?limit=50', headers=headers).json()
    flags = {m['id']: m['stub'] for m in body['memories']}
    assert flags == {'keep-1': False, 'stub-1': True}
    assert body['total'] == 2
