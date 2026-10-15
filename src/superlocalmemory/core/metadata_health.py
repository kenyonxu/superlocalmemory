# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Metadata coverage, runs and suggestions for doctor, the CLI and the Memory health card.

Everything a person reads is a plain sentence. Exception class names are
logged, never shown (WP-19 review L1).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from superlocalmemory.core.enrichment import status

logger = logging.getLogger(__name__)

STATES = {
    'waiting_for_idle': 'Waiting for your computer to be idle.',
    'queued': 'Waiting for your computer to be idle.',
    'running': 'Improving your memories in the background.',
    'paused': 'Paused. Your memories still work.',
    'completed': 'Up to date. Checked again while your computer is idle.',
    'undone': 'The last improvement was undone. Your memories still work.',
    'unavailable': 'Not available on this machine.',
}
WAITING = 'Some improvements are waiting and will be tried again.'
LABELS = {'enrichment': 'memory descriptions', 'entity-links': 'entity links',
          'refile-hidden': 'hidden memories that are still in use',
          'qjl-sketches': 'search sketches', 'repair-saves': 'saves re-filed'}
#: Repairs with no undo (core/health_repairs.NOT_UNDOABLE): the card says so
#: and asks first. Kept as literals so this read-only module imports nothing heavy.
NOT_UNDOABLE = ('repair-saves',)
NO_UNDO_NOTE = ("Fixing this can't be undone. A safety copy of your memories is taken "
                'first, and your memories stay as they are until you confirm.')


def sentence(action, found):
    """How a suggestion reads: one plain sentence, singular or plural."""
    if action == 'repair-saves':
        return f"{found} save{'' if found == 1 else 's'} didn't finish."
    return f"{found} {LABELS.get(action, action)} can be fixed."


def coverage(db):
    total = db.execute('SELECT COUNT(*) AS n FROM atomic_facts')[0]['n']
    counted = {
        'observation_dates': "observation_date IS NOT NULL AND observation_date!=''",
        'event_dates': "referenced_date IS NOT NULL AND referenced_date!=''",
        'valid_from': "valid_from IS NOT NULL AND valid_from!=''",
        'entities': "canonical_entities_json IS NOT NULL AND canonical_entities_json!='[]'",
        'kind_confirmed': "memory_kind_source IN ('caller','user')",
        'kind_model': "memory_kind_source LIKE 'model:%'",
        'kind_guessed': "memory_kind_source='rules'",
        'non_answer': 'COALESCE(non_answer,0)=1',
    }
    cols = {r['name'] for r in db.execute('PRAGMA table_info(atomic_facts)')}
    out = {'facts': total}
    for key, clause in counted.items():
        column = clause.split()[0].split('=')[0]
        if key == 'non_answer':
            column = 'non_answer'
        out[key] = (db.execute(f'SELECT COUNT(*) AS n FROM atomic_facts WHERE {clause}')
                    [0]['n'] if column in cols else 0)
    parents = db.execute('SELECT COUNT(*) AS n, '
                         "SUM(CASE WHEN json_valid(metadata_json) AND "
                         "json_extract(metadata_json,'$.tags') NOT IN ('','[]') THEN 1 ELSE 0 END)"
                         ' AS tags, '
                         "SUM(CASE WHEN json_valid(metadata_json) AND "
                         "json_extract(metadata_json,'$.project') NOT IN ('','[]') "
                         "THEN 1 ELSE 0 END)"
                         ' AS project FROM memories')[0]
    out.update(memories=parents['n'], tags=parents['tags'] or 0, project=parents['project'] or 0)
    return out


def _has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def runs(db, limit=50):
    """Every run that changed something, newest first, each with its run id."""
    out = []
    if _has_table(db, 'enrichment_runs'):
        out += [{**dict(r), 'kind': 'improvement', 'action': 'enrichment'}
                for r in db.execute(
                    'SELECT run_id,status,changed,created_at,updated_at FROM enrichment_runs '
                    "WHERE action='enrichment' AND changed>0")]
    if _has_table(db, 'memory_health_repairs'):
        out += [{**dict(r), 'kind': 'repair', 'updated_at': r['created_at']}
                for r in db.execute(
                    'SELECT run_id,action,status,fixed AS changed,created_at '
                    "FROM memory_health_repairs WHERE status IN ('completed','undone') "
                    'AND fixed>0')]
    for run in out:
        run['label'] = LABELS.get(run['action'], run['action'])
        run['undoable'] = run['action'] not in NOT_UNDOABLE
    return sorted(out, key=lambda r: r['created_at'] or '', reverse=True)[:limit]


def _suggestion(action, found):
    item = {'action': action, 'found': found, 'label': LABELS.get(action, action),
            'sentence': sentence(action, found), 'undoable': action not in NOT_UNDOABLE}
    if action == 'repair-saves':
        item.update(button='Fix', label='saves that did not finish', note=NO_UNDO_NOTE)
    return item


def suggestions(db):
    """Repairs that delete or move things: shown with a Fix button, never automatic."""
    if not _has_table(db, 'memory_health_repairs'):
        return []
    return [_suggestion(r['action'], r['found'])
            for r in db.execute("SELECT action,found FROM memory_health_repairs "
                                "WHERE status='suggested' AND found>0 ORDER BY action")]


def state_text(run):
    if run.get('last_error'):
        return WAITING
    return STATES.get(run.get('status'), STATES['waiting_for_idle'])


def report(db):
    current = status(db)
    counts = coverage(db)
    repairs = []
    if _has_table(db, 'memory_health_repairs'):
        repairs = [dict(r) for r in db.execute(
            "SELECT * FROM memory_health_repairs WHERE status!='suggested' "
            'ORDER BY rowid DESC LIMIT 20')]
    return {'enrichment': current, 'coverage': counts, 'repairs': repairs,
            'runs': runs(db), 'suggestions': suggestions(db), 'state': state_text(current),
            'summary': f"Improving your memories: {current.get('processed', 0):,} "
                       f"of {current.get('total', counts['facts']):,}"}


def doctor_checks(data):
    """``slm doctor`` rows (name, status, detail, fix) from a report."""
    cov, run = data.get('coverage', {}), data.get('enrichment', {})
    waiting = bool(run.get('last_error'))
    checks = [('Memory description', 'WARN' if waiting else 'PASS',
               f"Tags {cov.get('tags', 0)}/{cov.get('memories', 0)}; "
               f"kind confirmed {cov.get('kind_confirmed', 0)}, "
               f"model {cov.get('kind_model', 0)}, guessed {cov.get('kind_guessed', 0)}; "
               f"dates {cov.get('observation_dates', 0)}/{cov.get('facts', 0)}, "
               f"event dates {cov.get('event_dates', 0)}, valid from {cov.get('valid_from', 0)}; "
               f"entities {cov.get('entities', 0)}. "
               f"{data.get('state') or state_text(run)}",
               'slm enrich status' if waiting else '')]
    for item in data.get('suggestions') or []:
        checks.append(('Memory repairs', 'WARN',
                       f"{item.get('sentence') or str(item['found']) + ' ' + item['label']}"
                       ' Nothing is changed until you choose to (dashboard Memory health, '
                       'or the command below).',
                       f"slm enrich fix {item['action']}"))
    return checks


def from_path(db_path):
    from types import SimpleNamespace

    conn = None
    # A read-only report must not initialize, migrate or start any services.
    try:
        conn = sqlite3.connect(f'{Path(db_path).absolute().as_uri()}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        db = SimpleNamespace(execute=lambda sql, params=(): conn.execute(sql, params).fetchall())
        return report(db)
    except (sqlite3.Error, OSError) as exc:
        logger.debug('Memory health report unavailable (%s)', type(exc).__name__)
        return {'enrichment': {'status': 'unavailable'}, 'coverage': {}, 'runs': [],
                'suggestions': [], 'state': STATES['unavailable'],
                'summary': 'Memory health is temporarily unavailable.'}
    finally:
        if conn is not None:
            conn.close()
