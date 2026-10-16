"""Undo of the native entity-links repair: one total, and undone items stay hidden.

WP-19 re-check NEW-2 (the CLI printed 0 because the native undo has no
``restored`` key) and NEW-3 (``undone_items`` only read the enrichment ledger, so
the card re-suggested items the user had just undone).
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from tests.test_core.test_wp19_health_repairs import snapshot, store  # noqa: F401
from tests.test_storage.test_entity_link_repair import _build, _links, _state


@pytest.fixture
def engine(tmp_path):
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = _build(tmp_path / 'memory.db')
    with sqlite3.connect(db.db_path) as conn:
        apply(conn)
    yield SimpleNamespace(_db=db)
    db.close()


def _tick(engine):
    from superlocalmemory.core.health_repairs import run_safe_repairs
    engine._memory_health_checked = None
    run_safe_repairs(engine, idle=lambda: True)


def test_undo_reports_one_restored_total_and_keeps_the_details(engine):
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    fixed = repair_action(engine, 'entity-links', dry_run=False)
    assert fixed['fixed'] == 27
    undone = undo_action(engine, fixed['run_id'])
    assert undone['restored'] == 27
    assert (undone['facts_restored'], undone['aliases_restored'],
            undone['junk_entities_restored']) == (6, 16, 5)
    assert undone['conflicts'] == 0


def test_conflicts_are_the_facts_changed_since(engine):
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    fixed = repair_action(engine, 'entity-links', dry_run=False)
    engine._db.execute("UPDATE atomic_facts SET canonical_entities_json='[\"x\"]' "
                       "WHERE fact_id='f_pg'")
    undone = undo_action(engine, fixed['run_id'])
    assert undone['conflicts'] == 1 and undone['restored'] == 26


def test_an_ordinary_undo_also_has_a_restored_total(engine):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    from superlocalmemory.core.health_repairs import undo_action
    runner = EnrichmentRunner(engine._db, idle=lambda: True)
    for _ in range(20):
        run = runner.run_batch(limit=25)
        if run['status'] == 'completed':
            break
    undone = undo_action(engine, run['run_id'])
    assert undone['restored'] > 0 and 'conflicts' in undone


def test_items_of_an_undone_native_run_are_hidden_and_not_reapplied(engine):
    from superlocalmemory.core.health_repairs import repair_action, undo_action, undone_items
    from superlocalmemory.core.metadata_health import report
    db = engine._db
    start = _state(db.db_path)
    fixed = repair_action(engine, 'entity-links', dry_run=False)
    undo_action(engine, fixed['run_id'])
    assert _state(db.db_path) == start
    assert {'f_pg', 'e_users'} <= undone_items(db, 'entity-links')
    _tick(engine)
    assert 'entity-links' not in {s['action'] for s in report(db)['suggestions']}
    assert repair_action(engine, 'entity-links', dry_run=True)['found'] == 0
    again = repair_action(engine, 'entity-links', dry_run=False)
    assert again['fixed'] == 0
    assert _state(db.db_path) == start


def test_include_undone_reapplies_what_the_user_chooses(engine):
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    db = engine._db
    undo_action(engine, repair_action(engine, 'entity-links', dry_run=False)['run_id'])
    again = repair_action(engine, 'entity-links', dry_run=False, include_undone=True)
    assert again['fixed'] == 27
    assert _links(db)['f_pg'] == ['PostgreSQL']


def test_undone_refile_items_stay_hidden_until_included(store):  # noqa: F811
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    before = snapshot(store._db)
    undo_action(store, repair_action(store, 'refile-hidden', dry_run=False)['run_id'])
    _tick(store)
    assert repair_action(store, 'refile-hidden', dry_run=False)['fixed'] == 0
    assert snapshot(store._db) == before
    assert repair_action(store, 'refile-hidden', dry_run=False,
                         include_undone=True)['fixed'] == 2
