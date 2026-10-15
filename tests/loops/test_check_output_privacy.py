"""Verifier N1: neither raw key bytes nor their encodings leave in evidence."""

import base64
import hashlib
import json
import sys

from superlocalmemory.loops.execution_guard import _key
from superlocalmemory.loops.receipt_jobs import run_registered_check
from tests.loops.test_hermetic_contract import register
from tests.mcp.test_loop_escape_integrity import ledger as ledger
from tests.mcp.test_loop_escape_integrity import tools
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db


def test_old_ancestor_key_path_is_unavailable(receipt_db, tmp_path):
    _, _, job = register(receipt_db, tmp_path, code=(
        "from pathlib import Path\n"
        "p = Path('../../admission.key')\n"
        "raise SystemExit(1 if p.exists() else 0)\n"))
    assert run_registered_check(receipt_db, job['job_id'])['verdict'] == 'PASS'
    assert not (tmp_path / 'slm/loop-checks/admission.key').exists()


async def test_known_key_full_output_hash_and_redacted_excerpt(
    receipt_db, tmp_path, ledger, caplog
):
    # Same-user code can find the key via an explicit absolute path. Its output
    # must still be safe; this is a redaction guarantee, not an OS sandbox.
    vault = tmp_path / 'slm/loop-admission/admission.key'
    code = (
        "import sys,base64\nfrom pathlib import Path\n"
        f"key=Path({str(vault)!r}).read_bytes()\n"
        "sys.stdout.buffer.write(key)\n"
        "sys.stdout.flush()\n"
        "print(key.hex()); print(base64.b64encode(key).decode()); print(repr(key))\n"
        "print('Z'*12000)\n"
        "sys.stderr.buffer.write(key)\n")
    _, _, job = register(receipt_db, tmp_path, code=code)
    key = _key(job['store_path'])
    receipt = run_registered_check(receipt_db, job['job_id'])
    evidence = json.loads(receipt['evidence'])
    full = key + (key.hex() + '\n' + base64.b64encode(key).decode() + '\n'
                  + repr(key) + '\n' + 'Z' * 12000 + '\n').encode()
    hash_matches = evidence.get('stdout_sha256') == hashlib.sha256(full).hexdigest()
    assert hash_matches
    representations = (key.hex(), base64.b64encode(key).decode(), repr(key),
                       key.decode('utf-8', errors='replace'))
    leaked = any(value in json.dumps(receipt) or value in receipt['evidence']
                 for value in representations)
    assert not leaked
    # Receipts carry hashes only: no form of the output can leak through them.
    assert not any(k.endswith('_excerpt') for k in evidence)
    assert evidence['stderr_sha256'] == hashlib.sha256(key).hexdigest()
    assert len(evidence['output_sha256']) == 64
    assert vault.stat().st_mode & 0o777 == 0o600
    rows = receipt_db.execute('SELECT evidence FROM loop_execution_receipts')
    stored_leak = any(value in rows[0]['evidence'] for value in representations)
    assert not stored_leak
    fns = tools(receipt_db)
    result = await fns['slm_loop_run']('build', 'build', max_iterations=1,
                                      gate_contract={'job_id': job['job_id']})
    history = await fns['slm_loop_history']('build')
    show = await fns['slm_loop_show'](result['run_id'])
    response_leak = any(value in json.dumps([result, history, show]) or value in caplog.text
                        for value in representations)
    assert not response_leak


def test_existing_key_moves_without_invalidating_admission(receipt_db, tmp_path):
    from superlocalmemory.loops.execution_guard import verify_admission

    _, _, job = register(receipt_db, tmp_path)
    receipt = run_registered_check(receipt_db, job['job_id'])
    vault = tmp_path / 'slm/loop-admission/admission.key'
    old = tmp_path / 'slm/loop-checks/admission.key'
    vault.rename(old)
    _key(job['store_path'], create=True)
    assert vault.exists() and not old.exists()
    assert verify_admission(job, receipt)


def test_timeout_keeps_hash_of_all_captured_output(receipt_db, tmp_path):
    _, _, job = register(receipt_db, tmp_path, code=(
        "import sys,time\nprint('before timeout',flush=True)\n"
        "print('error detail',file=sys.stderr,flush=True)\ntime.sleep(10)\n"))
    receipt = run_registered_check(receipt_db, job['job_id'], timeout=0.2)
    evidence = json.loads(receipt['evidence'])
    assert receipt['verdict'] == 'FAIL'
    assert evidence['stdout_sha256'] == hashlib.sha256(b'before timeout\n').hexdigest()
    assert evidence['stderr_sha256'] == hashlib.sha256(b'error detail\n').hexdigest()
    reason = evidence['stop_reason']
    assert (reason == 'Independent check timed out'
            or reason.startswith('a process started by the check is still running:'))


def test_escaped_process_keeps_full_output_digest(receipt_db, tmp_path):
    _, _, job = register(receipt_db, tmp_path, code=(
        "import os,time\nprint('before escape',flush=True)\n"
        "if os.fork()==0:\n"
        "    os.setsid()\n"
        "    null=os.open(os.devnull,os.O_RDWR)\n"
        "    for fd in (0,1,2): os.dup2(null,fd)\n"
        "    time.sleep(10); os._exit(0)\n"))
    receipt = run_registered_check(receipt_db, job['job_id'])
    evidence = json.loads(receipt['evidence'])
    assert receipt['verdict'] == 'FAIL'
    assert evidence['stdout_sha256'] == hashlib.sha256(b'before escape\n').hexdigest()
    assert evidence['stop_reason'].startswith('a process started by the check is still running:')


def test_observable_admission_key_edit_refuses_receipt(receipt_db, tmp_path):
    import pytest

    vault = tmp_path / 'slm/loop-admission/admission.key'
    _, _, job = register(receipt_db, tmp_path, code=(
        "from pathlib import Path\n"
        f"Path({str(vault)!r}).write_bytes(b'N' * 32)\n"))
    with pytest.raises(ValueError, match='Admission key changed during verification'):
        run_registered_check(receipt_db, job['job_id'])


def test_pipe_holding_setsid_child_is_stopped_when_leader_exits(receipt_db, tmp_path):
    marker = tmp_path / 'escaped'
    _, _, job = register(receipt_db, tmp_path, code=(
        'import os,time\n'
        'if os.fork()==0:\n'
        '    os.setsid()\n'
        f"    time.sleep(0.5); open({str(marker)!r},'w').write('alive'); os._exit(0)\n"))
    receipt = run_registered_check(receipt_db, job['job_id'])
    assert not marker.exists()
    assert receipt['verdict'] == 'FAIL'
    assert json.loads(receipt['evidence'])['stop_reason'].startswith(
        'a process started by the check is still running:')


def test_known_key_output_cannot_admit_pass(receipt_db, tmp_path):
    vault = tmp_path / 'slm/loop-admission/admission.key'
    _, _, job = register(receipt_db, tmp_path, code=(
        'import sys\nfrom pathlib import Path\n'
        f'sys.stdout.buffer.write(Path({str(vault)!r}).read_bytes())\n'))
    assert run_registered_check(receipt_db, job['job_id'])['verdict'] == 'FAIL'


def test_internal_execution_keeps_text_triplet_contract(tmp_path):
    from superlocalmemory.loops.check_process import execute

    result = execute([sys.executable, '-I', '-c', "print('measured')"], tmp_path, 3)
    _, out, err = result
    assert out == 'measured\n' and err == ''
    assert result.stdout_bytes == b'measured\n'
