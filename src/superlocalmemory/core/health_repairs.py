# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Memory health repairs, using the product's native repair and undo machinery.

Two kinds, and the line between them is the point (WP-19 review H1):

* AUTO_ACTIONS only ADD what is missing (a search sketch rebuilt from the
  stored vector). The idle worker may run them, in small batches.
* EXPLICIT_ACTIONS delete or move things (entity aliases and junk entities,
  lifecycle zones, saves re-filed onto their own memory). The idle worker only
  counts them and stores a suggestion; they run when the user presses Fix
  (dashboard) or runs ``slm enrich fix``.

``repair-saves`` is ``slm ops repair-saves --apply`` (storage/save_repair): the
same plan and apply code, behind the shared safety copy. It keeps no undo
record, so it is in NOT_UNDOABLE: a Fix needs an explicit confirm and the run
refuses an undo with a plain sentence.

An undone run is final until the user says otherwise: an action whose latest run
was undone is never run automatically again, and the items an undone run touched
(from the enrichment ledger, or the native entity-link repair's own undo record)
are left out of every suggestion and every Fix. Only ``slm enrich fix <repair>
--include-undone`` applies them again.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from superlocalmemory.core.description_metadata import VERSION
from superlocalmemory.core.enrichment import EnrichmentRunner, now
from superlocalmemory.storage import enrichment_ledger as ledger

logger = logging.getLogger(__name__)

AUTO_ACTIONS = ('qjl-sketches',)
EXPLICIT_ACTIONS = ('refile-hidden', 'entity-links', 'repair-saves')
NOT_UNDOABLE = ('repair-saves',)
NO_UNDO = "Re-filed saves can't be undone: the repair keeps no undo record."
ACTIONS = (*EXPLICIT_ACTIONS, *AUTO_ACTIONS)
AUTO_BATCH = 25
CHECK_INTERVAL_S = 1800
BACKOFF_BASE_S = 1800
BACKOFF_MAX_S = 86400


def _record(db, run_id, action, found, fixed=0, *, error=None, native=None):
    db.execute("UPDATE enrichment_runs SET status='completed',updated_at=? WHERE run_id=?",
               (now(), run_id))
    db.execute('INSERT INTO memory_health_repairs(run_id,action,status,found,fixed,error,'
               'native_run_id,created_at,last_seen) VALUES (?,?,?,?,?,?,?,?,?)',
               (run_id, action, 'completed', found, fixed, error, native, now(), now()))


def _new_run(db, action):
    run_id, stamp = uuid.uuid4().hex, now()
    point = EnrichmentRunner(db, idle=lambda: True)._backup()
    db.execute('INSERT INTO enrichment_runs(run_id,enrichment_version,action,status,'
               'upper_rowid,total,backup_point,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
               (run_id, VERSION, action, 'running', 0, 0, point, stamp, stamp))
    return run_id


def _changes(db, run_id, table, before, fields):
    for old in before:
        after = db.execute(f'SELECT * FROM {table} WHERE fact_id=? AND profile_id=?',
                           (old['fact_id'], old['profile_id']))
        if not after:
            continue
        after = dict(after[0])
        for field in fields:
            if old[field] != after[field]:
                ledger.record(db, run_id, old['profile_id'], table, old['fact_id'],
                              field, old[field], after[field], fact_id=old['fact_id'])


def undone_items(db, action) -> frozenset:
    """Every item an undone run of *action* touched: hidden and not re-applied.

    Two ledgers: ``enrichment_changes`` (refile, sketches), and the native
    entity-link repair's own undo record, named by ``native_run_id``.
    """
    rows = db.execute(
        'SELECT DISTINCT c.target_id FROM enrichment_changes c '
        'JOIN memory_health_repairs r ON r.run_id=c.run_id '
        "WHERE r.action=? AND r.status='undone'", (action,))
    items = {r['target_id'] for r in rows}
    native = [r['native_run_id'] for r in db.execute(
        'SELECT native_run_id FROM memory_health_repairs WHERE action=? '
        "AND status='undone' AND native_run_id IS NOT NULL AND native_run_id!=''", (action,))]
    if native:
        from superlocalmemory.storage.entity_link_repair_apply import touched_ids
        items |= touched_ids(db.execute, native)
    return frozenset(items)


def _refile(engine, dry_run, idle, skip=frozenset()):
    from superlocalmemory.core.lifecycle_state import set_fact_lifecycle_zone
    from superlocalmemory.core.memory_health import refile_hidden

    db = engine._db
    plan = refile_hidden(db.db_path, dry_run=True)
    candidates = [f for f in plan.fact_ids if f not in skip]
    if dry_run or not candidates or not idle():
        return {'found': len(candidates), 'fixed': 0}
    run_id, fixed = _new_run(db, 'refile-hidden'), 0
    for fact_id in candidates:
        if not idle():
            break
        with db.transaction():
            retention = [dict(r) for r in db.execute(
                'SELECT * FROM fact_retention WHERE fact_id=?', (fact_id,))]
            if (not retention
                    or retention[0]['lifecycle_zone'] not in ('archive', 'forgotten')
                    or retention[0]['retention_score'] <= 0.8):
                continue
            facts = [dict(r) for r in db.execute('SELECT * FROM atomic_facts WHERE fact_id=?',
                                                (fact_id,))]
            set_fact_lifecycle_zone(db, [fact_id], 'active')
            _changes(db, run_id, 'atomic_facts', facts, ('lifecycle',))
            _changes(db, run_id, 'fact_retention', retention,
                     ('lifecycle_zone', 'last_computed_at'))
            fixed += 1
    _record(db, run_id, 'refile-hidden', len(candidates), fixed)
    return {'found': len(candidates), 'fixed': fixed, 'run_id': run_id}


def _quantizer(engine):
    from dataclasses import replace

    from superlocalmemory.math.polar_quant import PolarQuantEncoder
    from superlocalmemory.math.qjl import QJLEncoder
    from superlocalmemory.storage.quantized_store import QuantizedEmbeddingStore

    cfg = engine._config.quantization
    polar = PolarQuantEncoder(replace(cfg.polar, dimension=engine._config.embedding.dimension))
    return QuantizedEmbeddingStore(engine._db, polar, QJLEncoder(cfg.qjl), cfg), cfg


def _qjl_one(db, store, run_id, profile, row, original):
    from superlocalmemory.storage.qjl_repair import _repair_one

    with db.transaction():
        snapshots = {table: [dict(r) for r in db.execute(
            f'SELECT * FROM {table} WHERE fact_id=? AND profile_id=?',
            (row['fact_id'], profile))]
            for table in ('polar_embeddings', 'embedding_quantization_metadata')}
        _repair_one(db, store, row, profile, original[1], original[0])
        for table, fields in (('polar_embeddings', ('qjl_bits',)),
                              ('embedding_quantization_metadata', ('compressed_size_bytes',))):
            _changes(db, run_id, table, snapshots[table], fields)


def _qjl(engine, dry_run, idle, skip=frozenset()):
    from superlocalmemory.storage.qjl_repair import _original_snapshot, repair_rows

    db = engine._db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='polar_embeddings'"):
        return {'found': 0, 'fixed': 0}
    store, cfg = _quantizer(engine)
    profiles = db.execute('SELECT DISTINCT profile_id FROM polar_embeddings')
    plans = [(r['profile_id'], row) for r in profiles
             for row in repair_rows(db, r['profile_id'], cfg) if row['fact_id'] not in skip]
    if dry_run or not plans or not idle():
        return {'found': len(plans), 'fixed': 0}
    run_id, fixed, errors = _new_run(db, 'qjl-sketches'), 0, 0
    for profile, row in plans[:AUTO_BATCH]:
        if not idle():
            break
        original = _original_snapshot(db, row['fact_id'], profile, store._polar._d)
        if original is None:
            errors += 1
            continue
        try:
            _qjl_one(db, store, run_id, profile, row, original)
            fixed += 1
        except (ValueError, TypeError):
            errors += 1
    _record(db, run_id, 'qjl-sketches', len(plans), fixed,
            error='Some sketches need a new embedding' if errors else None)
    return {'found': len(plans), 'fixed': fixed, 'errors': errors, 'run_id': run_id}


def _entities(engine, dry_run, idle, skip=frozenset()):
    import sqlite3
    from contextlib import closing

    from superlocalmemory.storage.entity_link_repair import plan
    from superlocalmemory.storage.entity_link_repair_apply import apply

    db = engine._db
    uri = f'{Path(db.db_path).absolute().as_uri()}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        proposed = plan(conn, skip)
        found = sum(len(p.facts) + len(p.aliases) + len(p.junk) for p in proposed.profiles)
    if dry_run or not found or not idle():
        return {'found': found, 'fixed': 0}
    # The shared pre-change safety copy (taken once per store), then the
    # native repair, whose own run ledger is what undo replays. Explicit only:
    # the user asked, so the whole plan is applied in one native transaction.
    run_id = _new_run(db, 'entity-links')
    result = apply(db.db_path, skip)
    fixed = sum(result.get(k, 0) for k in ('facts_relinked', 'aliases_removed',
                                          'aliases_kept_relabelled', 'junk_entities_removed'))
    _record(db, run_id, 'entity-links', found, fixed, native=result.get('run_id'))
    return {'found': found, 'fixed': fixed, 'run_id': run_id}


def _saves(engine, dry_run, idle, skip=frozenset()):
    import sqlite3
    from contextlib import closing

    from superlocalmemory.storage import save_repair
    from superlocalmemory.storage.save_repair_apply import apply

    db = engine._db
    uri = f'{Path(db.db_path).absolute().as_uri()}?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('ingestion_operations','dead_letter_operations')")}
        if len(have) < 2:  # a store that never recorded saves has none to repair
            return {'found': 0, 'fixed': 0}
        proposed = save_repair.plan(conn)  # always the dry run first
    found = len(proposed.refile)
    if dry_run or not found or not idle():
        return {'found': found, 'fixed': 0}
    # The shared safety copy (once per store) replaces apply()'s own copy; the
    # apply re-checks every reviewed save inside its write transaction.
    run_id = _new_run(db, 'repair-saves')
    result = apply(Path(db.db_path), proposed, backup=False)
    _record(db, run_id, 'repair-saves', found, result.refiled)
    return {'found': found, 'fixed': result.refiled, 'skipped': result.skipped, 'run_id': run_id}


_RUNNERS = {'refile-hidden': _refile, 'qjl-sketches': _qjl, 'entity-links': _entities,
            'repair-saves': _saves}


def repair_action(engine, action, *, dry_run=True, idle=lambda: True, include_undone=False,
                  confirmed=False):
    """The user's explicit repair (dashboard Fix, ``slm enrich fix``) or a dry run.

    Items an earlier, undone run touched are left out unless ``include_undone``
    (``slm enrich fix <repair> --include-undone``) says the user wants them again.
    A repair with no undo (NOT_UNDOABLE) only runs when ``confirmed``.
    """
    if action not in ACTIONS:
        raise ValueError('Unknown memory health action')
    if action in NOT_UNDOABLE and not dry_run and not confirmed:
        raise ValueError(f'{NO_UNDO} Confirm to go ahead; a safety copy is taken first.')
    hidden = undone_items(engine._db, action)
    skip = frozenset() if include_undone else hidden
    result = {'action': action, **_RUNNERS[action](engine, dry_run, idle, skip)}
    if not dry_run and action in EXPLICIT_ACTIONS:
        remaining = _RUNNERS[action](engine, True, lambda: True, hidden)
        _suggest(engine._db, action, remaining['found'])
    return result


def _suggest(db, action, found):
    db.execute('INSERT INTO memory_health_repairs(run_id,action,status,found,fixed,'
               "created_at,last_seen) VALUES (?,?,'suggested',?,0,?,?) "
               'ON CONFLICT(run_id) DO UPDATE SET found=excluded.found,'
               'last_seen=excluded.last_seen',
               (f'suggest-{action}', action, int(found), now(), now()))


def _record_error(db, action, exc):
    # Error class only: messages can carry private memory text. One row per
    # action+error; repeats raise the count and move last_seen (review L2).
    name = type(exc).__name__
    logger.warning('Memory health %s deferred (%s)', action, name)
    db.execute('INSERT INTO memory_health_repairs(run_id,action,status,found,fixed,error,'
               "created_at,last_seen,attempts) VALUES (?,?,'error',0,0,?,?,?,1) "
               'ON CONFLICT(run_id) DO UPDATE SET attempts=attempts+1,'
               'last_seen=excluded.last_seen',
               (f'error-{action}-{name}'[:64], action, name, now(), now()))


def _backed_off(db, action) -> bool:
    rows = db.execute("SELECT attempts,last_seen FROM memory_health_repairs WHERE action=? "
                      "AND status='error' ORDER BY last_seen DESC LIMIT 1", (action,))
    if not rows or not rows[0]['last_seen']:
        return False
    wait = min(BACKOFF_BASE_S * 2 ** (max(1, rows[0]['attempts']) - 1), BACKOFF_MAX_S)
    seen = datetime.fromisoformat(rows[0]['last_seen'])
    return datetime.now(UTC) - seen < timedelta(seconds=wait)


def _latest_undone(db, action) -> bool:
    rows = db.execute("SELECT status FROM memory_health_repairs WHERE action=? "
                      "AND status IN ('completed','undone') ORDER BY rowid DESC LIMIT 1",
                      (action,))
    return bool(rows) and rows[0]['status'] == 'undone'


def _tick_action(engine, action, idle):
    """One action on an idle tick. Returns True when more batches remain."""
    db = engine._db
    if _backed_off(db, action):
        return False
    try:
        if action in EXPLICIT_ACTIONS:
            found = _RUNNERS[action](engine, True, idle, undone_items(db, action))['found']
            _suggest(db, action, found)
            return False
        if _latest_undone(db, action):
            return False
        result = _RUNNERS[action](engine, False, idle, undone_items(db, action))
        db.execute("DELETE FROM memory_health_repairs WHERE action=? AND status='error'",
                   (action,))
        return bool(result.get('fixed')) and result['fixed'] < result['found']
    except Exception as exc:
        _record_error(db, action, exc)
        return False


def run_safe_repairs(engine, *, idle):
    # ``None`` means "never checked". A 0.0 sentinel is wrong here: the monotonic
    # clock counts from boot on Linux, so on a host up for less than
    # CHECK_INTERVAL_S (a fresh CI runner, a just-restarted server) ``now - 0.0``
    # is still below the interval and the first check would be skipped.
    last = getattr(engine, '_memory_health_checked', None)
    if last is not None and time.monotonic() - last < CHECK_INTERVAL_S:
        return
    engine._memory_health_checked = time.monotonic()
    more = False
    for action in ACTIONS:
        if not idle():
            return
        more = _tick_action(engine, action, idle) or more
    if more:  # continue bounded automatic batches on the next idle tick
        engine._memory_health_checked = None


def _native_total(native):
    """One ``restored`` total (what the CLI and card show) plus the native counts.

    Facts, aliases and removed entities are the changes a Fix counts as ``fixed``,
    so "Fixed 27" is undone as "27 changes restored". Edges, events and created
    entities are consequences of those, kept in the detail only.
    """
    total = sum(native.get(k, 0) for k in ('facts_restored', 'aliases_restored',
                                          'junk_entities_restored'))
    return {**native, 'restored': total,
            'conflicts': native.get('facts_changed_since_left_alone', 0)}


def undo_action(engine, run_id):
    db = engine._db
    rows = db.execute('SELECT * FROM memory_health_repairs WHERE run_id=?', (run_id,))
    if rows and rows[0]['action'] in NOT_UNDOABLE:
        raise ValueError(NO_UNDO)
    if rows and rows[0]['status'] == 'undone':
        raise ValueError('That run was already undone.')
    if rows and rows[0]['status'] != 'completed':
        raise ValueError('That is not a run that changed anything.')
    if rows and rows[0]['native_run_id']:
        from superlocalmemory.storage.entity_link_repair_apply import undo
        result = _native_total(undo(db.db_path, rows[0]['native_run_id']))
        db.execute("UPDATE enrichment_runs SET status='undone',updated_at=? WHERE run_id=?",
                   (now(), run_id))
    else:
        result = EnrichmentRunner(db, idle=lambda: False).undo(run_id)
    db.execute("UPDATE memory_health_repairs SET status='undone',last_seen=? WHERE run_id=?",
               (now(), run_id))
    return result
