"""Execution-window ABA probes with pre-existing, verdict-neutral handshakes."""

import os
import sys
import threading
import time

import pytest

from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check
from tests.loops.hermetic_fixtures import private_data_root as private_data_root
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db
from tests.mcp.test_loop_receipt_races import receipts
from tests.mcp.test_loop_receipt_replay import harness


def ready(path):
    deadline = time.monotonic() + 5
    while path.read_text() != "ready":
        if time.monotonic() > deadline:
            raise TimeoutError(path.name)
        time.sleep(.005)


def setup_job(db, root, kind):
    work, control = root / "work", root / "control"
    if kind == "ancestor":
        work = root / "grand" / "parent" / "work"
    work.mkdir(parents=True)
    control.mkdir()
    for marker in ("started", "go", "read_done", "restored"):
        (control / marker).write_text("")
    inputs = work / "inputs"
    inputs.mkdir()
    state, script = inputs / "state", work / "verdict.py"
    state.write_text("fail")
    script.write_text("passed = False")
    if kind == "alias":
        state.rename(inputs / "failing")
        (inputs / "passing").write_text("pass")
        state.symlink_to("failing")
    alternate = root / "alternate"
    alternate.mkdir()
    (alternate / "state").write_text("pass")
    (alternate / "inputs").mkdir()
    (alternate / "inputs" / "state").write_text("pass")
    if kind == "ancestor":
        replacement = alternate / "parent" / "work" / "inputs"
        replacement.mkdir(parents=True)
        (replacement / "state").write_text("pass")
    command = bootstrap(control, state, script, kind)
    job = register_job(
        db, profile_id="default", task_id="build", watcher_name="build", producer="checker",
        command=command, cwd=work, artifact_paths=[state], check_paths=[script],
        allow_prestart_evidence=True,
    )
    return work, control, state, script, alternate, job


def bootstrap(control, state, script, kind):
    code = f"""from pathlib import Path
import time, runpy
control = Path({str(control)!r})
def wait(name):
    deadline = time.monotonic() + 5
    while (control / name).read_text() != 'ready':
        if time.monotonic() > deadline: raise SystemExit(2)
        time.sleep(.005)
(control / 'started').write_text('ready')
wait('go')
passed = (runpy.run_path('verdict.py')['passed'] if {kind!r} == 'script'
          else Path('inputs/state').read_text() == 'pass')
print('read pass' if passed else 'read fail', flush=True)
(control / 'read_done').write_text('ready')
wait('restored')
raise SystemExit(0 if passed else 1)
"""
    return [sys.executable, "-I", "-c", code]


def switch(kind, work, control, state, script, alternate):
    if kind in ("bytes", "script"):
        target = script if kind == "script" else state
        before, info = target.read_bytes(), target.stat()
        target.write_text("passed = True" if kind == "script" else "pass")

        def restore():
            target.write_bytes(before)
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))

        return restore
    if kind == "alias":
        state.unlink()
        state.symlink_to("passing")

        def restore():
            state.unlink()
            state.symlink_to("failing")

        return restore
    target = {
        "file": state, "directory": work / "inputs", "cwd": work,
        "ancestor": work.parent.parent,
    }[kind]
    passing = alternate / "state" if kind == "file" else alternate
    held = control / "held"
    target.rename(held)
    passing.rename(target)

    def restore():
        target.rename(passing)
        held.rename(target)

    return restore


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["bytes", "script", "file", "alias", "directory", "cwd", "ancestor"],
)
async def test_aba_window_never_publishes_pass(receipt_db, tmp_path, monkeypatch, kind):
    from superlocalmemory.loops import execution_receipts

    if kind == "alias":
        with pytest.raises(ValueError, match="symlink"):
            setup_job(receipt_db, tmp_path, kind)
        return
    work, control, state, script, alternate, job = setup_job(receipt_db, tmp_path, kind)
    measurements, errors = [], []
    execute = execution_receipts._execution

    def measured(*args):
        result = execute(*args)
        measurements.append(result)
        return result

    def mutate():
        try:
            ready(control / "started")
            restore = switch(kind, work, control, state, script, alternate)
            (control / "go").write_text("ready")
            ready(control / "read_done")
            restore()
            (control / "restored").write_text("ready")
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(execution_receipts, "_execution", measured)
    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        assert run_registered_check(receipt_db, job["job_id"])["verdict"] == "FAIL"
    finally:
        thread.join(6)
    assert not errors and not thread.is_alive()
    assert measurements[0][0] == 1 and "read fail" in measurements[0][1]
    assert state.read_text() == "fail" and script.read_text() == "passed = False"
    assert all(row["verdict"] == "FAIL" for row in receipts(receipt_db))
    result = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]},
    )
    assert result["status"] == "HALT" and not result["passed"], result
