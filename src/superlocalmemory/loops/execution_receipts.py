# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Operator-side execution receipts. This writer is never an MCP memory tool.

Only an actual subprocess supplies output and exit status. Agents' remember
claims, attribution and metadata cannot create execution evidence. The table is
append-only; its integrity boundary is the owned service, not filesystem admin.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from superlocalmemory.loops.check_output import output_evidence
from superlocalmemory.loops.check_process import execute

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS loop_execution_receipts ("
    "receipt_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, task_id TEXT NOT NULL, "
    "run_id TEXT NOT NULL, producer TEXT NOT NULL, command_digest TEXT NOT NULL, "
    "verdict TEXT NOT NULL, exit_code INTEGER NOT NULL, produced_at TEXT NOT NULL, "
    "evidence_digest TEXT NOT NULL, evidence TEXT NOT NULL, "
    "FOREIGN KEY(profile_id) REFERENCES profiles(profile_id) ON DELETE CASCADE, "
    "CHECK ((exit_code=0 AND verdict='PASS') OR (exit_code!=0 AND verdict='FAIL')))"
)


def command_digest(command, cwd):
    payload = json.dumps({"argv": list(command), "cwd": str(Path(cwd).resolve())}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _execution(command, cwd, timeout):
    if (
        not isinstance(command, (list, tuple))
        or not command
        or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in command)
    ):
        raise ValueError("Check command must be a nonempty argv sequence")
    return execute(command, cwd, timeout)


def _store(db, receipt):
    db.execute(_SCHEMA)
    for operation in ("UPDATE", "DELETE"):
        guard = (
            " WHEN EXISTS (SELECT 1 FROM profiles WHERE profile_id=OLD.profile_id)"
            if operation == "DELETE"
            else ""
        )
        db.execute(
            f"CREATE TRIGGER IF NOT EXISTS loop_receipts_no_{operation.lower()} "
            f"BEFORE {operation} ON loop_execution_receipts{guard} BEGIN "
            "SELECT RAISE(ABORT,'execution receipts are append-only'); END"
        )
    fields = tuple(receipt)
    db.execute(
        f"INSERT INTO loop_execution_receipts ({','.join(fields)}) "
        f"VALUES ({','.join('?' for _ in fields)})",
        tuple(receipt.values()),
    )


def run_check(
    db, *, profile_id, task_id, run_id, producer, command, cwd, timeout=30,
    validate_result=None,
):
    """Persist a diagnostic execution; public callbacks cannot authorize DONE.

    There are deliberately no verdict, status, digest or output input parameters.
    A waiting MCP agent has no tool that can call this writer with its own claims.
    Measurement stays private until the callback succeeds; a refusal inserts
    no receipt. Its return value is discarded. Only the private registered
    runner authenticates admitted measurements after hermetic validation.
    """
    for value in (profile_id, task_id, run_id, producer):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Execution identity fields must be nonempty strings")
    result = _execution(command, cwd, min(max(float(timeout), 0.1), 60))
    exit_code = result[0]
    stdout = getattr(result, 'stdout_bytes', result[1])
    stderr = getattr(result, 'stderr_bytes', result[2])
    if validate_result is not None:
        validate_result()
    receipt = _measurement(profile_id, task_id, run_id, producer,
                           command_digest(command, cwd), exit_code, stdout, stderr,
                           stop_reason=getattr(result, "reason", None))
    _store(db, receipt)
    return receipt


def _measurement(profile_id, task_id, run_id, producer, digest, exit_code, stdout, stderr,
                 secret_material=(), stop_reason=None):
    output = output_evidence(stdout, stderr, secret_material)
    if output['key_exposed']:
        exit_code = -1 if exit_code == 0 else exit_code
        stop_reason = stop_reason or 'Independent check exposed admission key material'
    measurement = dict(exit_code=exit_code, **output)
    if stop_reason:
        # Product-written text, never check output.
        measurement['stop_reason'] = str(stop_reason)[:512]
    evidence = json.dumps(measurement, sort_keys=True)
    receipt = dict(
        receipt_id=uuid.uuid4().hex,
        profile_id=profile_id,
        task_id=task_id,
        run_id=run_id,
        producer=producer,
        command_digest=digest,
        verdict="PASS" if exit_code == 0 else "FAIL",
        exit_code=exit_code,
        produced_at=datetime.now(UTC).isoformat(),
        evidence_digest=hashlib.sha256(evidence.encode()).hexdigest(),
        evidence=evidence,
    )
    return receipt


def _run_registered(db, job, command, cwd, timeout, validate_result):
    """Only this runner signs, after the complete validation callback returns."""
    from superlocalmemory.loops.execution_guard import _key, _sign_receipt

    key = _key(job["store_path"])
    result = _execution(command, cwd, min(max(float(timeout), 0.1), 60))
    if not hmac.compare_digest(key, _key(job['store_path'])):
        raise ValueError('Admission key changed during verification')
    validate_result()
    receipt = _measurement(job["profile_id"], job["task_id"], job["run_id"], job["producer"],
                           job["command_digest"], result[0],
                           getattr(result, 'stdout_bytes', result[1]),
                           getattr(result, 'stderr_bytes', result[2]), secret_material=(key,),
                           stop_reason=getattr(result, "reason", None))
    _sign_receipt(job, receipt)
    _store(db, receipt)
    return receipt
