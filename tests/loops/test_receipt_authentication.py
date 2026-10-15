"""A diagnostic writer or forged measurement cannot reuse genuine admission."""

import hashlib
import json

import pytest

from superlocalmemory.loops.execution_guard import verify_admission
from superlocalmemory.loops.execution_receipts import run_check
from superlocalmemory.loops.receipt_jobs import run_registered_check
from tests.loops.test_hermetic_contract import passes, register
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db


@pytest.mark.parametrize('field', [
    'receipt_id', 'profile_id', 'task_id', 'run_id', 'producer', 'command_digest',
    'produced_at', 'exit_code', 'verdict', 'output', 'revision_digest', 'definition_digest',
])
def test_authentication_binds_all_measurement_and_job_fields(receipt_db, tmp_path, field):
    _, _, job = register(receipt_db, tmp_path)
    receipt = run_registered_check(receipt_db, job['job_id'])
    assert verify_admission(job, receipt)
    if field in {'output', 'revision_digest', 'definition_digest'}:
        value = json.loads(receipt['evidence'])
        if field == 'output':
            value['stdout_sha256'] = '0' * 64
        else:
            value['admission'][field] = '0' * 64
        receipt['evidence'] = json.dumps(value, sort_keys=True)
        receipt['evidence_digest'] = hashlib.sha256(receipt['evidence'].encode()).hexdigest()
    elif field == 'exit_code':
        receipt[field] = 1
    else:
        receipt[field] += '-forged'
    assert not verify_admission(job, receipt)


def test_public_callback_cannot_reuse_genuine_admission(receipt_db, tmp_path):
    work, _, job = register(receipt_db, tmp_path)
    genuine = run_registered_check(receipt_db, job['job_id'])
    assert passes(receipt_db, job)
    proof = json.loads(genuine['evidence'])['admission']
    raw = run_check(
        receipt_db, profile_id=job['profile_id'], task_id=job['task_id'],
        run_id=job['run_id'], producer=job['producer'],
        command=json.loads(job['command_json']), cwd=work,
        validate_result=lambda: proof,
    )
    assert raw['verdict'] == 'PASS' and 'admission' not in json.loads(raw['evidence'])
    assert not passes(receipt_db, job)
