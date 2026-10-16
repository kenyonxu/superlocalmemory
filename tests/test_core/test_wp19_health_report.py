"""Every changing run is reachable for undo, in plain words (review M5, L1)."""

import pytest

from tests.test_core.test_wp19_health_repairs import _tick, store  # noqa: F401


def _finish(db):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(10):
        result = runner.run_batch(limit=10)
        if result['status'] == 'completed':
            return result
    raise AssertionError('enrichment did not finish')


def test_report_lists_every_changing_run_with_its_id(store):  # noqa: F811
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    from superlocalmemory.core.metadata_health import report
    db = store._db
    improved = _finish(db)
    fixed = repair_action(store, 'refile-hidden', dry_run=False)
    runs = report(db)['runs']
    assert [r['run_id'] for r in runs][:2] == [fixed['run_id'], improved['run_id']]
    assert runs[0]['kind'] == 'repair' and runs[0]['changed'] == 2
    assert runs[1]['kind'] == 'improvement' and runs[1]['changed'] > 0
    assert all(r['status'] == 'completed' for r in runs)
    undo_action(store, fixed['run_id'])
    assert report(db)['runs'][0]['status'] == 'undone'


def test_suggestions_and_plain_state_never_name_an_exception(store):  # noqa: F811
    from superlocalmemory.core.metadata_health import report
    db = store._db
    assert report(db)['state'] == 'Waiting for your computer to be idle.'
    _tick(store)
    suggestions = {s['action']: s['found'] for s in report(db)['suggestions']}
    assert suggestions == {'refile-hidden': 2}
    _finish(db)
    db.execute("UPDATE enrichment_runs SET last_error='OperationalError'")
    state = report(db)['state']
    assert 'OperationalError' not in state and 'waiting' in state.lower()


@pytest.mark.parametrize('kind', ['repair', 'improvement'])
def test_undoing_twice_says_so_plainly(store, kind):  # noqa: F811
    from superlocalmemory.core.health_repairs import repair_action, undo_action
    db = store._db
    run_id = (repair_action(store, 'refile-hidden', dry_run=False)['run_id']
              if kind == 'repair' else _finish(db)['run_id'])
    undo_action(store, run_id)
    with pytest.raises(ValueError, match='^That run was already undone.$'):
        undo_action(store, run_id)
    with pytest.raises(ValueError, match='^That run was not found.$'):
        undo_action(store, 'no-such-run')


def test_doctor_report_hides_exception_names(tmp_path):
    from superlocalmemory.core.metadata_health import from_path
    missing = from_path(tmp_path / 'absent' / 'memory.db')
    assert 'error' not in missing
    assert missing['state'] == 'Not available on this machine.'


def test_doctor_shows_suggested_repairs_as_plain_advice():
    from superlocalmemory.core.metadata_health import doctor_checks
    report = {'coverage': {'facts': 10, 'memories': 10}, 'enrichment': {'status': 'completed'},
              'state': 'Up to date. Checked again while your computer is idle.',
              'suggestions': [{'action': 'entity-links', 'found': 35, 'label': 'entity links'}]}
    checks = {name: (status, detail, fix) for name, status, detail, fix in doctor_checks(report)}
    assert checks['Memory description'][0] == 'PASS'
    assert 'Up to date' in checks['Memory description'][1]
    status, detail, fix = checks['Memory repairs']
    assert status == 'WARN' and '35 entity links' in detail
    assert fix == 'slm enrich fix entity-links'
    quiet = doctor_checks({**report, 'suggestions': []})
    assert [c[0] for c in quiet] == ['Memory description']
