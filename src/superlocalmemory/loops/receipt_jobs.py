# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Operator registrations select immutable checks and mutable declared inputs.

No MCP mutation can register or execute a job. A rerun may check a new artifact
revision with the same pinned definition; DONE always copies current inputs.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from superlocalmemory.infra.data_root import canonical_data_root
from superlocalmemory.loops import artifact_revision
from superlocalmemory.loops.artifact_revision import copy_inputs, matches
from superlocalmemory.loops.check_definition import materialize, normalized, prepare, relocated
from superlocalmemory.loops.check_lock import exclusive_check
from superlocalmemory.loops.execution_guard import _key
from superlocalmemory.loops.execution_receipts import _run_registered, command_digest
from superlocalmemory.loops.private_workspace import (
    platform_reason,
    private_dir,
    run_workspace,
    temporary,
)
from superlocalmemory.loops.pytest_config import prepare_pytest, validate_pytest_configuration

_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS loop_gate_jobs ('
    'job_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, task_id TEXT NOT NULL, '
    'watcher_name TEXT NOT NULL, run_id TEXT NOT NULL, producer TEXT NOT NULL, '
    'command_digest TEXT NOT NULL, command_json TEXT NOT NULL, cwd TEXT NOT NULL, '
    'artifacts_json TEXT NOT NULL, revision_digest TEXT NOT NULL, '
    'registered_at TEXT NOT NULL, expires_at TEXT NOT NULL, '
    'allow_prestart_evidence INTEGER NOT NULL CHECK(allow_prestart_evidence IN (0,1)), '
    'artifact_paths_json TEXT, definition_json TEXT, definition_digest TEXT, store_path TEXT, '
    'FOREIGN KEY(profile_id) REFERENCES profiles(profile_id) ON DELETE CASCADE)'
)


def _store(db, job):
    db.execute(_SCHEMA)
    columns = {row['name'] for row in db.execute('PRAGMA table_info(loop_gate_jobs)')}
    for column in ('artifact_paths_json', 'definition_json', 'definition_digest', 'store_path'):
        if column not in columns:
            db.execute(f'ALTER TABLE loop_gate_jobs ADD COLUMN {column} TEXT')
    for operation in ('UPDATE', 'DELETE'):
        guard = (
            ' WHEN EXISTS (SELECT 1 FROM profiles WHERE profile_id=OLD.profile_id)'
            if operation == 'DELETE' else ''
        )
        db.execute(
            f'CREATE TRIGGER IF NOT EXISTS loop_jobs_no_{operation.lower()} '
            f'BEFORE {operation} ON loop_gate_jobs{guard} BEGIN '
            "SELECT RAISE(ABORT,'gate registrations are operator-owned and immutable'); END"
        )
    fields = tuple(job)
    db.execute(
        f"INSERT INTO loop_gate_jobs ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
        tuple(job.values()),
    )


def _registration_storage(cwd, data_root):
    reason = platform_reason()
    if reason:
        raise ValueError(reason)
    root = Path(data_root).absolute() if data_root is not None else canonical_data_root()
    store_path = root / 'loop-checks'
    if store_path.resolve().is_relative_to(Path(cwd)):
        raise ValueError('Hermetic check data root must be outside the approved project directory')
    store = private_dir(store_path)
    _key(store, create=True)
    return store


def register_job(
    db, *, profile_id, task_id, watcher_name, producer, command, cwd, artifact_paths,
    check_paths=(), data_root=None, allow_prestart_evidence=False, ttl_seconds=3600,
):
    """Pin tests/scripts/config separately from explicit mutable artifact paths/globs.

    The interpreter/tool installation is operator trusted. Check paths are
    frozen at registration; do not include them in artifact_paths. No symlinks,
    devices or FIFOs are permitted. Unsupported no-follow hosts refuse here.
    """
    for value in (profile_id, task_id, watcher_name, producer):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Gate identity fields must be nonempty strings')
    if not isinstance(command, (list, tuple)) or not command or not all(
        isinstance(arg, str) and arg and '\x00' not in arg for arg in command
    ):
        raise ValueError('Gate check must be a nonempty argv sequence')
    if type(allow_prestart_evidence) is not bool or not 0 < float(ttl_seconds) <= 86400:
        raise ValueError('Gate lifetime must be positive and at most one day')
    cwd = str(Path(cwd).resolve())
    command = normalized(command, cwd)
    store = _registration_storage(cwd, data_root)
    now, nonce = datetime.now(UTC), uuid.uuid4().hex
    job = dict(
        job_id=nonce, profile_id=profile_id, task_id=task_id, watcher_name=watcher_name,
        run_id=nonce, producer=producer, command_digest=command_digest(command, cwd),
        command_json=json.dumps(list(command)), cwd=cwd, registered_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=float(ttl_seconds))).isoformat(),
        allow_prestart_evidence=int(allow_prestart_evidence),
    )
    prepare(job, command, cwd, check_paths, artifact_paths, store)
    _store(db, job)
    return job


def current_job(db, job_id, profile_id, watcher_name):
    rows = db.execute(
        'SELECT * FROM loop_gate_jobs WHERE profile_id=? AND watcher_name=? '
        'ORDER BY rowid DESC LIMIT 1', (profile_id, watcher_name),
    )
    if not rows:
        return None
    job = dict(rows[0])
    if (job['job_id'] != job_id or not job.get('definition_digest')
            or datetime.now(UTC) >= datetime.fromisoformat(job['expires_at'])):
        return None
    return job


def _validate_execution(db, job, run_dir, artifacts):
    """All checks finish before any authenticated measurement is published."""
    validate_pytest_configuration(job, run_dir)
    if not matches(job):
        raise ValueError('Checked artifacts changed during verification')
    if not current_job(db, job['job_id'], job['profile_id'], job['watcher_name']):
        raise ValueError('Gate job expired or superseded during verification')
    names = [r['path'] for r in json.loads(artifacts)]
    with temporary() as copy:
        copied, _ = copy_inputs(run_dir, names, copy, allow_empty=True)
    if copied != artifacts:
        raise ValueError('Check modified its copied inputs during verification')
    with temporary() as copy:
        materialize(job, copy)
        expected = job['definition_json']
    with temporary() as copy:
        actual, _ = copy_inputs(run_dir, [r['path'] for r in json.loads(expected)],
                                copy, allow_empty=True)
    if actual != expected:
        raise ValueError('Check modified its copied definition during verification')


def run_registered_check(db, job_id, *, timeout=30):
    """Execute only the operator's pinned definition in a fresh private directory."""
    rows = db.execute('SELECT * FROM loop_gate_jobs WHERE job_id=?', (job_id,))
    if not rows:
        raise ValueError('No such operator gate job')
    job = dict(rows[0])
    if not current_job(db, job_id, job['profile_id'], job['watcher_name']):
        raise ValueError('Gate job expired, superseded or requires hermetic re-registration')
    reason = platform_reason()
    if reason:
        raise ValueError(reason)
    with exclusive_check(job['store_path']), run_workspace(job) as run_dir:
        materialize(job, run_dir)
        artifacts, revision = artifact_revision.copy_inputs(
            job['cwd'], json.loads(job['artifact_paths_json']), run_dir, allow_empty=True)
        candidate = dict(job, revision_digest=revision)
        command = relocated(json.loads(job['command_json']), job['cwd'], run_dir)
        command = prepare_pytest(command, run_dir, candidate)
        return _run_registered(
            db, candidate, command, run_dir, timeout,
            lambda: _validate_execution(db, candidate, run_dir, artifacts),
        )
