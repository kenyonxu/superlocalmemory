"""BRN-08: real check interleavings must never publish rejected PASS evidence."""

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check
from tests.loops.hermetic_fixtures import private_data_root as private_data_root
from tests.mcp.test_loop_independent_receipts import _threaded_receipt_connection
from tests.mcp.test_loop_receipt_replay import harness


@pytest.fixture
def receipt_db():
    conn = _threaded_receipt_connection()
    yield SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    conn.close()


def receipts(db):
    if not db.execute("SELECT name FROM sqlite_master WHERE name='loop_execution_receipts'"):
        return []
    return db.execute("SELECT * FROM loop_execution_receipts")


def register(db, root, script, artifacts):
    return register_job(
        db, profile_id="default", task_id="build", watcher_name="build", producer="checker",
        command=[sys.executable, "-I", str(script)], cwd=root,
        artifact_paths=[p for p in artifacts if p != script], check_paths=[script],
        allow_prestart_evidence=True,
    )


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(path.name)
        time.sleep(.005)


def race_job(db, root):
    work, control = root / "work", root / "control"
    work.mkdir()
    control.mkdir()
    state, script = work / "state", work / "check.py"
    state.write_text("fail")
    script.write_text(f"""from pathlib import Path
import time
control = Path({str(control)!r})
(control / 'started').touch()
deadline = time.monotonic() + 5
for marker in ('go',):
    while not (control / marker).exists():
        if time.monotonic() > deadline: raise SystemExit(2)
        time.sleep(.005)
passed = Path('state').read_text() == 'pass'
print('read pass' if passed else 'read fail', flush=True)
(control / 'read_done').touch()
while not (control / 'restored').exists():
    if time.monotonic() > deadline: raise SystemExit(2)
    time.sleep(.005)
raise SystemExit(0 if passed else 1)
""")
    return state, register(db, work, script, [state, script])


def writer(root, state, *, restore):
    root = root / "control"
    errors = []

    def mutate():
        try:
            wait_for(root / "started")
            state.write_text("pass")
            (root / "go").touch()
            wait_for(root / "read_done")
            if restore:
                state.write_text("fail")
            (root / "restored").touch()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=mutate)
    thread.start()
    return thread, errors


@pytest.mark.asyncio
async def test_rejected_check_leaves_no_usable_pass(receipt_db, tmp_path):
    state, job = race_job(receipt_db, tmp_path)
    thread, errors = writer(tmp_path, state, restore=False)
    try:
        with pytest.raises(ValueError, match="changed during verification"):
            run_registered_check(receipt_db, job["job_id"])
    finally:
        thread.join(6)
    assert not errors and not thread.is_alive()
    state.write_text("fail")
    assert not receipts(receipt_db), "Rejected execution already published completion evidence"
    result = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
    )
    assert result["status"] == "HALT" and not result["passed"], result


@pytest.mark.asyncio
async def test_pass_is_invisible_until_postvalidation_finishes(receipt_db, tmp_path, monkeypatch):
    from superlocalmemory.loops import receipt_jobs

    script = tmp_path / "check.py"
    script.write_text("print('real subprocess passed')")
    job = register(receipt_db, tmp_path, script, [script])
    entered, release = threading.Event(), threading.Event()
    original, calls = receipt_jobs.matches, 0

    def paused_validation(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            if not release.wait(5):
                raise TimeoutError("validation release")
            return False
        return original(candidate)

    monkeypatch.setattr(receipt_jobs, "matches", paused_validation)
    task = asyncio.create_task(asyncio.to_thread(run_registered_check, receipt_db, job["job_id"]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        result = await harness(receipt_db)(
            "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
        )
        assert result["status"] == "HALT" and not result["passed"], result
        assert not receipts(receipt_db)
    finally:
        release.set()
        with pytest.raises(ValueError, match="changed during verification"):
            await task
    assert not receipts(receipt_db)


@pytest.mark.parametrize("change", ["superseded", "expired"])
def test_postexecution_job_refusal_never_publishes(receipt_db, tmp_path, change):
    work, control = tmp_path / "work", tmp_path / "control"
    work.mkdir()
    control.mkdir()
    script = work / "check.py"
    script.write_text(f"""from pathlib import Path
import time
control = Path({str(control)!r})
(control / 'started').touch()
deadline = time.monotonic() + 5
while not (control / 'release').exists():
    if time.monotonic() > deadline: raise SystemExit(2)
    time.sleep(.005)
print('passed')
""")
    job = register_job(
        receipt_db, profile_id="default", task_id="build", watcher_name="build",
        producer="checker", command=[sys.executable, "-I", str(script)], cwd=work,
        artifact_paths=[], check_paths=[script], allow_prestart_evidence=True,
        ttl_seconds=.1 if change == "expired" else 3600,
    )
    errors = []

    def invalidate():
        try:
            wait_for(control / "started")
            if change == "superseded":
                register(receipt_db, work, script, [script])
            else:
                time.sleep(.15)
            (control / "release").touch()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=invalidate)
    thread.start()
    try:
        with pytest.raises(ValueError, match="expired or superseded during verification"):
            run_registered_check(receipt_db, job["job_id"])
    finally:
        thread.join(6)
    assert not errors and not thread.is_alive()
    assert not receipts(receipt_db)


@pytest.mark.asyncio
async def test_changed_and_restored_bytes_cannot_certify_failing_revision(receipt_db, tmp_path):
    state, job = race_job(receipt_db, tmp_path)
    thread, errors = writer(tmp_path, state, restore=True)
    try:
        assert run_registered_check(receipt_db, job["job_id"])["verdict"] == "FAIL"
    finally:
        thread.join(6)
    assert not errors and not thread.is_alive()
    assert state.read_text() == "fail"
    assert all(row["verdict"] == "FAIL" for row in receipts(receipt_db))
    result = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
    )
    assert result["status"] == "HALT" and not result["passed"], result


@pytest.mark.asyncio
async def test_legacy_unvalidated_registered_pass_is_ineligible(receipt_db, tmp_path):
    import json

    from superlocalmemory.loops.execution_receipts import run_check

    state, job = race_job(receipt_db, tmp_path)
    thread, errors = writer(tmp_path, state, restore=True)
    try:
        receipt = run_check(
            receipt_db, profile_id=job["profile_id"], task_id=job["task_id"],
            run_id=job["run_id"], producer=job["producer"],
            command=json.loads(job["command_json"]), cwd=job["cwd"],
        )
    finally:
        thread.join(6)
    assert not errors and not thread.is_alive()
    assert receipt["verdict"] == "PASS" and state.read_text() == "fail"
    result = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
    )
    assert result["status"] == "HALT" and not result["passed"], result


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
async def test_stable_inputs_publish_admitted_receipt_with_outputs(receipt_db, tmp_path, verdict):
    import json

    state, script = tmp_path / "state", tmp_path / "check.py"
    state.write_text("pass" if verdict == "PASS" else "fail")
    (tmp_path / "outputs").mkdir()
    script.write_text("""from pathlib import Path
passed = Path('state').read_text() == 'pass'
Path('outputs').mkdir()
Path('outputs/result').write_text(str(passed))
raise SystemExit(0 if passed else 1)
""")
    job = register(receipt_db, tmp_path, script, [state, script])
    receipt = run_registered_check(receipt_db, job["job_id"])
    assert receipt["verdict"] == verdict
    proof = json.loads(receipt["evidence"])["admission"]
    assert proof["job_id"] == job["job_id"]
    assert proof["revision_digest"] == job["revision_digest"]
    assert proof["definition_digest"] == job["definition_digest"]
    assert proof["validation"] == "hermetic-copy-v1" and proof["signature"]
    result = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
    )
    assert result["status"] == ("DONE" if verdict == "PASS" else "HALT"), result
