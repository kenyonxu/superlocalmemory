# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Default-on, resumable metadata enrichment. Each fact is one short transaction."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from superlocalmemory.core.description_metadata import VERSION, fact_description, parent_labels
from superlocalmemory.encoding.non_answer import user_decided
from superlocalmemory.storage import enrichment_ledger as ledger
from superlocalmemory.storage.entity_index import record_fact_entities

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC).isoformat()


def status(db):
    try:
        rows = db.execute("SELECT * FROM enrichment_runs WHERE action=? "
                          "ORDER BY rowid DESC LIMIT 1", ('enrichment',))
        return dict(rows[0]) if rows else {'status': 'waiting_for_idle', 'processed': 0, 'total': 0}
    except Exception as exc:
        # Logged, never shown: the card and CLI speak plain language (review L1).
        logger.debug('Memory improvements status unavailable (%s)', type(exc).__name__)
        return {'status': 'unavailable', 'processed': 0, 'total': 0}


class EnrichmentRunner:
    """A bounded unit of idle work; the daemon owns its scheduling and RAM gate."""

    def __init__(self, db, *, idle, classifier=None):
        self.db = db
        self.idle = idle
        self.classifier = classifier

    def _backup(self):
        """The product's pre-change safety copy, taken once per store.

        Every later run is undoable from its own ledger, so a copy per batch,
        per repair or per daily pass would only fill the disk. The first copy
        (before anything was changed) is the one worth keeping.
        """
        earlier = self.db.execute(
            "SELECT backup_point FROM enrichment_runs WHERE backup_point IS NOT NULL "
            "AND backup_point!='' AND enrichment_version=? ORDER BY rowid LIMIT 1", (VERSION,))
        if earlier:
            return earlier[0]['backup_point']
        from superlocalmemory.storage.backup import _pre_migration_backup
        root = Path(self.db.db_path).parent
        point = _pre_migration_backup(
            root / 'learning.db', Path(self.db.db_path),
            backups_root=root / 'pre-migration-snapshots',
            reason='memory-description', databases=frozenset({'memory'}))
        manifests = sorted(point.glob('manifest-*-pre-migration.json'))
        if not manifests:
            raise RuntimeError('The memory safety copy could not be verified')
        return manifests[-1].name

    def _run(self):
        current = status(self.db)
        if current['status'] in ('queued', 'running', 'paused', 'undone'):
            return current
        if current['status'] == 'completed' and current.get('enrichment_version') == VERSION:
            # Once a day, a new pass catches old imports and newly available models.
            age = datetime.now(UTC) - datetime.fromisoformat(current['updated_at'])
            if age.total_seconds() < 86400:
                return current
        # A later pass of the same version starts where the last one ended:
        # facts saved since then (imports, older clients) are the only ones the
        # fixed save pipeline may not have described.
        start = (current.get('upper_rowid') or 0
                 if current['status'] == 'completed'
                 and current.get('enrichment_version') == VERSION else 0)
        counts = self.db.execute('SELECT COUNT(*) AS n,COALESCE(MAX(rowid),0) AS last '
                                 'FROM atomic_facts WHERE rowid>?', (start,))[0]
        if start and not counts['n']:
            return current
        run_id, stamp = uuid.uuid4().hex, now()
        self.db.execute(
            'INSERT INTO enrichment_runs(run_id,enrichment_version,status,cursor_rowid,'
            'upper_rowid,total,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (run_id, VERSION, 'queued', start, counts['last'], counts['n'], stamp, stamp))
        return status(self.db)

    def pause(self, run_id):
        self.db.execute("UPDATE enrichment_runs SET status='paused',updated_at=? "
                        "WHERE run_id=? AND status IN ('queued','running')", (now(), run_id))
        return status(self.db)

    def resume(self, run_id):
        self.db.execute("UPDATE enrichment_runs SET status='queued',updated_at=? "
                        "WHERE run_id=? AND status='paused'", (now(), run_id))
        return status(self.db)

    def undo(self, run_id):
        with self.db.transaction():
            rows = self.db.execute('SELECT status FROM enrichment_runs WHERE run_id=?', (run_id,))
            if not rows:
                raise ValueError('That run was not found.')
            if rows[0]['status'] == 'undone':
                raise ValueError('That run was already undone.')
            result = ledger.undo_changes(self.db, run_id)
            self.db.execute("UPDATE enrichment_runs SET status='undone',updated_at=? "
                            "WHERE run_id=?",
                            (now(), run_id))
        return result

    def run_batch(self, *, limit=25):
        if not self.idle():
            return {**status(self.db), 'status': 'waiting_for_idle'}
        run = self._run()
        if run['status'] in ('paused', 'completed', 'undone'):
            return run
        try:
            if not run.get('backup_point'):
                point = self._backup()
                self.db.execute('UPDATE enrichment_runs SET backup_point=? WHERE run_id=?',
                                (point, run['run_id']))
            self._batch(run, max(1, min(100, int(limit))))
        except Exception as exc:
            # Error class only: database/model messages can contain private memory text.
            self.db.execute('UPDATE enrichment_runs SET errors=errors+1,last_error=?,updated_at=? '
                            'WHERE run_id=?', (type(exc).__name__, now(), run['run_id']))
            logger.warning('Memory improvements deferred (%s)', type(exc).__name__)
        return status(self.db)

    def _batch(self, run, limit):
        rows = self.db.execute(
            'SELECT rowid AS enrichment_rowid,fact_id,profile_id FROM atomic_facts '
            'WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
            (run['cursor_rowid'], run['upper_rowid'], limit))
        deadline = time.monotonic() + 2.0
        for row in rows:
            if not self.idle() or time.monotonic() >= deadline:
                return
            if status(self.db)['status'] not in ('queued', 'running'):
                return
            fact = self.db.get_fact(row['fact_id'], row['profile_id'])
            if fact is None:
                continue
            parents = self.db.execute('SELECT * FROM memories WHERE memory_id=? AND profile_id=?',
                                      (fact.memory_id, fact.profile_id))
            if not parents:
                self._checkpoint(run['run_id'], row['enrichment_rowid'], 0)
                continue
            memory = dict(parents[0])
            description = fact_description(fact, memory)
            labels = parent_labels(self.db, memory)
            assignment = self._kind(fact)
            if not self.idle():
                return
            with self.db.transaction():
                if status(self.db)['status'] not in ('queued', 'running') or not self.idle():
                    return
                changed = self._apply(run['run_id'], fact, description, labels, assignment)
                self._checkpoint(run['run_id'], row['enrichment_rowid'], changed)
        remaining = self.db.execute('SELECT 1 FROM atomic_facts WHERE rowid>? AND rowid<=? LIMIT 1',
                                    (status(self.db).get('cursor_rowid', 0), run['upper_rowid']))
        if not remaining:
            self.db.execute("UPDATE enrichment_runs SET status='completed',updated_at=? "
                            "WHERE run_id=?",
                            (now(), run['run_id']))

    def _checkpoint(self, run_id, rowid, changed):
        # A batch that got through clears an earlier transient error.
        self.db.execute("UPDATE enrichment_runs SET cursor_rowid=?,processed=processed+1,"
                        "changed=changed+?,status='running',last_error=NULL,updated_at=? "
                        "WHERE run_id=?",
                        (rowid, changed, now(), run_id))

    def _kind(self, fact):
        if fact.memory_kind_source not in (None, '', 'rules'):
            return None
        if self.classifier is None:
            if fact.memory_kind_source == 'rules':
                return None
            from superlocalmemory.encoding.memory_kind_classifier import _rules_for
            return _rules_for(fact)
        assignment = self.classifier.suggest([fact], caller_kind=None)[0]
        if assignment and assignment.source.value == 'rules' and fact.memory_kind_source == 'rules':
            return None
        return assignment

    def _entities(self, run_id, fact, names):
        ids = list(fact.canonical_entities)
        from superlocalmemory.storage.models import CanonicalEntity
        for name in names:
            entity = self.db.get_entity_by_name(name, fact.profile_id)
            if entity is None:
                entity = CanonicalEntity(profile_id=fact.profile_id, canonical_name=name)
                self.db.store_entity(entity)
                row = dict(self.db.execute(
                    'SELECT * FROM canonical_entities WHERE entity_id=?', (entity.entity_id,))[0])
                ledger.record(self.db, run_id, fact.profile_id, 'canonical_entities',
                              entity.entity_id, '__row__', None, row, fact_id=fact.fact_id)
            if entity.entity_id not in ids:
                ids.append(entity.entity_id)
        return ids

    def _apply(self, run_id, fact, description, labels, assignment):
        changed = 0
        def add(table, target, field, value, **kw):
            return ledger.add(self.db, run_id, fact.profile_id, table, target, field, value, **kw)
        user_said = user_decided(self.db, fact.fact_id, fact.profile_id)
        for field, value in description.items():
            if field == 'non_answer' and user_said:
                continue  # the user's "this is an answer" always wins
            if field == 'entities_json':
                value = json.dumps(list(dict.fromkeys([*fact.entities, *value])))
            inferred = field == 'observation_date' and not fact.observation_date
            changed += add('atomic_facts', fact.fact_id, field, value,
                           replace_guess=field == 'entities_json',
                           source=ledger.INFERRED if inferred else 'enrichment')
        for field, value in labels.items():
            changed += add('memories', fact.memory_id, field, value)
        current = dict(self.db.execute(
            'SELECT * FROM atomic_facts WHERE fact_id=? AND profile_id=?',
                                        (fact.fact_id, fact.profile_id))[0])
        # Event time comes only from the fact's own dates: its event date, else
        # the observation date it carried BEFORE this pass. An observation date
        # enrichment filled from created_at is save time and never a start.
        start = current['referenced_date'] or fact.observation_date
        changed += add('atomic_facts', fact.fact_id, 'valid_from', start)
        # A missing temporal row is legacy_unknown (storage/database.py): it is
        # never created here, because its transaction time would be invented.
        # An existing row only has an empty event start filled (add() is
        # fill-only); its system_created_at is never touched.
        changed += add('fact_temporal_validity', fact.fact_id, 'valid_from', start)
        ids = self._entities(run_id, fact, description['entities_json'])
        changed += add('atomic_facts', fact.fact_id, 'canonical_entities_json',
                       json.dumps(ids), replace_guess=True)
        record_fact_entities(self.db, fact.fact_id, fact.profile_id, ids)
        if assignment and current.get('memory_kind_source') not in ('caller', 'user'):
            # Rules never replace a model suggestion. A local model may refine rules.
            if current.get('memory_kind_source') in (None, '', 'rules'):
                for field, value in assignment.as_columns(now()).items():
                    changed += add('atomic_facts', fact.fact_id, field, value, replace_guess=True)
        return int(bool(changed))
