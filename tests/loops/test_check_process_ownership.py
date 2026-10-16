"""Only processes belonging to a check may prevent its receipt from passing."""

import json
import os
import re
import subprocess
import sys
import time

from superlocalmemory.loops import check_process
from superlocalmemory.loops.receipt_jobs import run_registered_check
from tests.loops.test_hermetic_contract import register
from tests.mcp.test_loop_escape_integrity import ledger as ledger
from tests.mcp.test_loop_escape_integrity import tools
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db

LINGERING = re.compile(r'^a process started by the check is still running: \S.* \(\d+\)$')


async def test_unrelated_process_started_during_check_does_not_halt(
    receipt_db, tmp_path, monkeypatch, ledger
):
    ready, release = tmp_path / 'ready', tmp_path / 'release'
    _, _, job = register(receipt_db, tmp_path, code=(
        'import time\nfrom pathlib import Path\n'
        f'Path({str(ready)!r}).touch()\n'
        f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n'))
    collect, siblings = check_process._collect_until_exit, []

    def start_unrelated(process, timeout, *args):
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), 'check never reached the rendezvous'
        env = dict(os.environ)
        env.pop('SLM_LOOP_RUN_TOKEN', None)
        siblings.append(subprocess.Popen(
            [sys.executable, '-I', '-c', 'import time; time.sleep(20)'],
            env=env, start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        release.touch()
        return collect(process, timeout, *args)

    monkeypatch.setattr(check_process, '_collect_until_exit', start_unrelated)
    try:
        receipt = run_registered_check(receipt_db, job['job_id'])
        result = await tools(receipt_db)['slm_loop_run'](
            'build', 'build', max_iterations=1, gate_contract={'job_id': job['job_id']})
        assert receipt['verdict'] == 'PASS'
        assert result['status'] == 'DONE'
        assert siblings[0].poll() is None, 'sweep killed an unrelated process'
    finally:
        for sibling in siblings:
            if sibling.poll() is None:
                sibling.kill()
            sibling.wait()


async def test_reparented_setsid_lurker_with_inherited_token_still_halts(
    receipt_db, tmp_path, ledger
):
    marker = tmp_path / 'lurker-ran'
    _, _, job = register(receipt_db, tmp_path, code=(
        'import os,time\n'
        'if os.fork()==0:\n'
        '    os.setsid()\n'
        '    if os.fork()==0:\n'
        '        null=os.open(os.devnull,os.O_RDWR)\n'
        '        for fd in (0,1,2): os.dup2(null,fd)\n'
        f"        time.sleep(1); open({str(marker)!r},'w').write('alive')\n"
        '        time.sleep(2)\n'
        '    os._exit(0)\n'
        'else:\n'
        '    os.wait()\n'))
    receipt = run_registered_check(receipt_db, job['job_id'])
    result = await tools(receipt_db)['slm_loop_run'](
        'build', 'build', max_iterations=1, gate_contract={'job_id': job['job_id']})
    assert receipt['verdict'] == 'FAIL'
    assert result['status'] == 'HALT'
    assert LINGERING.match(json.loads(receipt['evidence'])['stop_reason'])
    assert LINGERING.match(result['receipt_reason'])
    time.sleep(1.1)
    assert not marker.exists()


async def test_reparented_grandchild_with_scrubbed_environment_still_halts(
    receipt_db, tmp_path, ledger
):
    # The grandchild leaves the process group, loses its parent, and execs without the
    # token; the session recorded at spawn still ties it to the check.
    marker = tmp_path / 'grandchild-ran'
    _, _, job = register(receipt_db, tmp_path, code=(
        'import os,sys,time\n'
        'if os.fork()==0:\n'
        '    if os.fork()==0:\n'
        '        os.setpgid(0,0)\n'
        '        null=os.open(os.devnull,os.O_RDWR)\n'
        '        for fd in (0,1,2): os.dup2(null,fd)\n'
        '        time.sleep(.2)\n'
        '        os.execve(sys.executable,[sys.executable,"-I","-c",'
        f'"import time; time.sleep(1); open({str(marker)!r},\'w\').write(\'alive\'); '
        'time.sleep(2)"],{})\n'
        '    os._exit(0)\n'
        'os.wait()\n'
        'time.sleep(.4)\n'))
    receipt = run_registered_check(receipt_db, job['job_id'])
    result = await tools(receipt_db)['slm_loop_run'](
        'build', 'build', max_iterations=1, gate_contract={'job_id': job['job_id']})
    assert receipt['verdict'] == 'FAIL'
    assert result['status'] == 'HALT'
    assert LINGERING.match(json.loads(receipt['evidence'])['stop_reason'])
    time.sleep(1.1)
    assert not marker.exists()
