"""Every list and search surface shows a stored stub with the same flag (review H3)."""

from __future__ import annotations

import asyncio
from argparse import Namespace

from fastapi.testclient import TestClient

from superlocalmemory.storage.models import AtomicFact, FactType, MemoryRecord
from tests.test_parity.test_kind_surface_parity import _cli_json, _FakeEngine, _Server

STUB = 'Session ID: 9f8e7d6c5b4a'
REAL = 'Session notes: Zephyr chose PostgreSQL.'


def test_list_and_search_surfaces_flag_the_stub(tmp_path, monkeypatch, capsys) -> None:
    from superlocalmemory.cli import commands
    from superlocalmemory.core import engine as engine_mod
    from superlocalmemory.mcp import tools_core
    from superlocalmemory.server.unified_daemon import create_app
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager

    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    for content in (STUB, REAL):
        memory_id = db.store_memory(MemoryRecord(profile_id='default', content=content))
        db.store_fact(AtomicFact(profile_id='default', memory_id=memory_id, content=content,
                                 fact_type=FactType.SEMANTIC))
    engine = _FakeEngine(db)
    monkeypatch.setattr(engine_mod, 'MemoryEngine', lambda *a, **k: engine)
    server = _Server()
    tools_core.register_core_tools(server, lambda: engine)
    app = create_app()
    app.state.engine = engine

    def flags(results):
        return {r['content'][:20]: r['stub'] for r in results}

    expected = {STUB[:20]: True, REAL[:20]: False}
    assert flags(asyncio.run(server.captured['list_recent'](limit=10))['results']) == expected
    assert flags(asyncio.run(server.captured['search'](query='Session'))['results']) == expected
    cli = _cli_json(capsys, commands.cmd_list, Namespace(json=True, limit=10, kind=''))
    assert flags(cli['data']['results']) == expected
    assert flags(TestClient(app).get('/list').json()['results']) == expected
