# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Completion requires authenticated execution of the currently copied revision."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime

from superlocalmemory.loops.artifact_revision import matches
from superlocalmemory.loops.execution_guard import verify_admission
from superlocalmemory.loops.private_workspace import platform_reason


def _time(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _admitted(contract, running_agent):
    return (
        isinstance(contract, dict) and set(contract) == {'job_id'}
        and isinstance(contract['job_id'], str)
        and bool(re.fullmatch(r'[a-f0-9]{32}', contract['job_id']))
    )


def _valid(receipt, minimum, job):
    evidence = json.loads(receipt['evidence'])
    return (
        type(receipt['exit_code']) is int
        and ((receipt['verdict'] == 'PASS' and receipt['exit_code'] == 0)
             or (receipt['verdict'] == 'FAIL' and receipt['exit_code'] != 0))
        and minimum <= _time(receipt['produced_at']) <= datetime.now(UTC)
        and hashlib.sha256(receipt['evidence'].encode()).hexdigest() == receipt['evidence_digest']
        and isinstance(evidence, dict) and evidence.get('exit_code') == receipt['exit_code']
        and verify_admission(job, receipt)
    )


def _receipt_status(db, job, contract, running_agent, watcher_name, started_at):
    from superlocalmemory.loops.receipt_jobs import current_job

    if job['producer'] == running_agent:
        return False, 'receipt producer is the running agent'
    minimum = _time(job['registered_at'])
    if not job['allow_prestart_evidence']:
        minimum = max(minimum, _time(started_at) if started_at else datetime.now(UTC))
    fields = ('task_id', 'run_id', 'producer', 'command_digest')
    rows = db.execute(
        'SELECT * FROM loop_execution_receipts WHERE profile_id=? AND '
        + ' AND '.join(field + '=?' for field in fields)
        + ' ORDER BY julianday(produced_at) DESC,rowid DESC LIMIT 1',
        (job['profile_id'], *(job[field] for field in fields)),
    )
    if not rows:
        return False, 'no execution receipt'
    receipt = dict(rows[0])
    if not _valid(receipt, minimum, job):
        return False, 'latest receipt stale or unauthenticated'
    if receipt['verdict'] == 'FAIL':
        evidence = json.loads(receipt['evidence'])
        return False, evidence.get('stop_reason') or f"check FAILED (exit {receipt['exit_code']})"
    proof = json.loads(receipt['evidence'])['admission']
    if not matches(dict(job, revision_digest=proof['revision_digest'])):
        return False, 'declared artifact revision changed; rerun the registered check'
    if not current_job(db, contract['job_id'], job['profile_id'], watcher_name):
        return False, 'job expired or superseded'
    return True, 'authenticated PASS for current copied revision'


def receipt_status(engine, contract, running_agent, *, watcher_name='', started_at=None):
    reason = platform_reason()
    if reason:
        return False, reason
    if not _admitted(contract, running_agent):
        return False, 'operator-registered job_id required'
    db = getattr(engine, '_db', None) or engine.db
    try:
        from superlocalmemory.loops.receipt_jobs import current_job

        job = current_job(db, contract['job_id'], engine.profile_id, watcher_name)
        if job is None:
            return False, 'job missing, expired, superseded or requires hermetic re-registration'
        return _receipt_status(db, job, contract, running_agent, watcher_name, started_at)
    except sqlite3.Error:
        return False, 'no registered job or execution receipt'
    except (ValueError, TypeError, KeyError, OSError):
        return False, 'check definition or declared inputs unavailable or invalid'


def receipt_passes(engine, results, contract, running_agent, *, watcher_name='', started_at=None):
    return receipt_status(engine, contract, running_agent,
                          watcher_name=watcher_name, started_at=started_at)[0]
