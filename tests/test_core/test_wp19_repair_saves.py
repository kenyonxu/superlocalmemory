# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""WP-19 B7: saves that did not finish are one of the Memory health repairs.

Suggestion and an explicit Fix only (never the idle worker), the same code path
as ``slm ops repair-saves --apply``, the shared one-per-store safety copy, and a
plain refusal to undo because the repair keeps no undo record.
"""

# ruff: noqa: F811 (pytest fixtures are imported and redeclared as parameters)

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.core import health_repairs, metadata_health
from superlocalmemory.server.routes import memory_health
from tests.test_governance.test_saves_refiled_after_scope_drift import drifted  # noqa: F401


@pytest.fixture
def engine(drifted):  # noqa: F811
    engine = drifted[0]
    with sqlite3.connect(engine._config.db_path) as conn:
        from superlocalmemory.storage.migrations.M060_memory_description import apply
        apply(conn)
    engine._copies_before = _copies(engine)  # the store's own set-up may have left copies
    return engine


def _ops(engine):
    return [dict(r) for r in engine._db.execute(
        'SELECT operation_id,state,attempt_count FROM ingestion_operations ORDER BY operation_id')]


def _tick(engine):
    engine._memory_health_checked = None
    health_repairs.run_safe_repairs(engine, idle=lambda: True)


def _copies(engine):
    root = Path(engine._db.db_path).parent
    return (sorted((root / 'pre-migration-snapshots').glob('*-pre-migration.db')),
            sorted(root.glob('*before-save-repair*')))


def test_it_is_explicit_only_never_run_by_the_idle_worker(engine):
    assert 'repair-saves' in health_repairs.EXPLICIT_ACTIONS
    assert 'repair-saves' not in health_repairs.AUTO_ACTIONS
    before = _ops(engine)
    _tick(engine)
    _tick(engine)
    assert _ops(engine) == before
    assert _copies(engine) == engine._copies_before
    [item] = [s for s in metadata_health.suggestions(engine._db) if s['action'] == 'repair-saves']
    assert item['found'] == 3


def test_the_suggestion_is_plain_words_and_says_it_cannot_be_undone(engine):
    _tick(engine)
    [item] = [s for s in metadata_health.suggestions(engine._db) if s['action'] == 'repair-saves']
    assert item['sentence'] == "3 saves didn't finish."
    assert item['button'] == 'Fix'
    assert item['undoable'] is False
    assert "can't be undone" in item['note']


def test_a_single_save_reads_in_the_singular():
    assert metadata_health.sentence('repair-saves', 1) == "1 save didn't finish."


def test_doctor_lists_it_with_the_command_to_run(engine):
    _tick(engine)
    checks = metadata_health.doctor_checks(metadata_health.report(engine._db))
    [row] = [c for c in checks if c[0] == 'Memory repairs' and 'finish' in c[2]]
    assert row[1] == 'WARN'
    assert row[3] == 'slm enrich fix repair-saves'
    assert "3 saves didn't finish" in row[2]


def test_dry_run_changes_nothing(engine):
    before = _ops(engine)
    result = health_repairs.repair_action(engine, 'repair-saves', dry_run=True)
    assert (result['found'], result['fixed']) == (3, 0)
    assert _ops(engine) == before
    assert _copies(engine) == engine._copies_before


def test_fix_needs_a_confirm_because_it_has_no_undo(engine):
    before = _ops(engine)
    with pytest.raises(ValueError, match="can't be undone"):
        health_repairs.repair_action(engine, 'repair-saves', dry_run=False)
    assert _ops(engine) == before


def test_fix_takes_the_shared_safety_copy_once_then_refiles(engine):
    result = health_repairs.repair_action(engine, 'repair-saves', dry_run=False, confirmed=True)
    assert (result['found'], result['fixed']) == (3, 3)
    shared, own = _copies(engine)
    assert len(shared) == len(engine._copies_before[0]) + 1  # one shared copy ...
    assert own == []  # ... and not apply()'s own second one
    states = {r['operation_id']: r['state'] for r in _ops(engine)}
    assert sorted(states.values()).count('queryable') >= 3
    row = engine._db.execute("SELECT * FROM memory_health_repairs WHERE action='repair-saves' "
                             "AND status='completed'")[0]
    assert (row['found'], row['fixed']) == (3, 3)
    # Nothing is left to suggest (the one left for review is not a failed save to fix).
    assert not [s for s in metadata_health.suggestions(engine._db)
                if s['action'] == 'repair-saves']
    health_repairs.repair_action(engine, 'repair-saves', dry_run=False, confirmed=True)
    assert _copies(engine)[0] == shared


def test_it_matches_the_ops_command_result(engine):
    from superlocalmemory.storage import save_repair
    with sqlite3.connect(engine._config.db_path) as conn:
        planned = save_repair.plan(conn)
    result = health_repairs.repair_action(engine, 'repair-saves', dry_run=False, confirmed=True)
    assert result['found'] == len(planned.refile)


def test_undo_says_plainly_it_is_not_supported(engine):
    run = health_repairs.repair_action(engine, 'repair-saves', dry_run=False, confirmed=True)
    with pytest.raises(ValueError, match="can't be undone"):
        health_repairs.undo_action(engine, run['run_id'])
    [listed] = [r for r in metadata_health.runs(engine._db) if r['run_id'] == run['run_id']]
    assert listed['undoable'] is False
    assert listed['label'] == 'saves re-filed'


def _client(engine, monkeypatch):
    monkeypatch.setattr(memory_health, 'require_permission', lambda *a, **k: None)
    monkeypatch.setattr(memory_health.write_identity, 'require_write_actor',
                        lambda *a, **k: 'owner')
    app = FastAPI()
    app.include_router(memory_health.router)
    app.state.engine = engine
    return TestClient(app)


def test_route_refuses_without_confirm_then_applies(engine, monkeypatch):
    body = {'action': 'repair', 'repair': 'repair-saves'}
    with _client(engine, monkeypatch) as session:
        refused = session.post('/api/memory-health/action', json=body)
        assert refused.status_code == 422
        assert "can't be undone" in refused.json()['detail']
        done = session.post('/api/memory-health/action', json={**body, 'confirm': True})
        assert done.status_code == 200
        assert done.json()['fixed'] == 3


def test_route_uses_manage_permission(engine, monkeypatch):
    from fastapi import HTTPException

    def refuse(*args, **kwargs):
        raise HTTPException(403, 'Owner permission required')
    with _client(engine, monkeypatch) as session:
        monkeypatch.setattr(memory_health, 'require_permission', refuse)
        before = _ops(engine)
        response = session.post('/api/memory-health/action',
                                json={'action': 'repair', 'repair': 'repair-saves',
                                      'confirm': True})
        assert response.status_code == 403
        assert _ops(engine) == before


def test_ops_repair_saves_still_backs_up_by_itself(engine):
    from superlocalmemory.storage import save_repair
    from superlocalmemory.storage.save_repair_apply import apply
    with sqlite3.connect(engine._config.db_path) as conn:
        planned = save_repair.plan(conn)
    result = apply(engine._config.db_path, planned)
    assert Path(result.backup).is_file()
