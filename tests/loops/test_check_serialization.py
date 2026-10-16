"""A second job cannot enter its private copy before the first validation ends."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from superlocalmemory.loops import private_workspace, receipt_jobs
from tests.loops.test_hermetic_contract import register
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db


def test_root_lock_covers_validation_and_next_job(receipt_db, tmp_path, monkeypatch):
    _, _, first = register(receipt_db, tmp_path)
    second = receipt_jobs.register_job(
        receipt_db, profile_id='default', task_id='build', watcher_name='other', producer='checker',
        command=json.loads(first['command_json']), cwd=first['cwd'], check_paths=['check.py'],
        artifact_paths=['state'], data_root=tmp_path / 'slm', allow_prestart_evidence=True,
    )
    validated, release, entered = threading.Event(), threading.Event(), threading.Event()
    original = receipt_jobs._validate_execution

    def validate(db, job, run_dir, artifacts):
        if job['job_id'] == first['job_id']:
            validated.set()
            assert release.wait(5)
        else:
            entered.set()
        return original(db, job, run_dir, artifacts)

    monkeypatch.setattr(receipt_jobs, '_validate_execution', validate)
    copies = tmp_path / 'copies'
    copies.mkdir()
    monkeypatch.setattr(receipt_jobs, 'run_workspace',
                        lambda job: private_workspace.temporary(copies))
    with ThreadPoolExecutor(max_workers=2) as workers:
        one = workers.submit(receipt_jobs.run_registered_check, receipt_db, first['job_id'])
        assert validated.wait(5)
        two = workers.submit(receipt_jobs.run_registered_check, receipt_db, second['job_id'])
        try:
            assert not entered.wait(0.2)
            assert len(list(copies.iterdir())) == 1
        finally:
            release.set()
        assert one.result()['verdict'] == two.result()['verdict'] == 'PASS'
        assert entered.is_set()
