"""The DONE authority binds copied inputs and an operator-pinned definition."""

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from superlocalmemory.loops import execution_receipts, receipt_jobs
from superlocalmemory.loops.receipt_gate import receipt_passes
from tests.mcp.test_loop_receipt_races import receipt_db as receipt_db


def register(db, tmp_path, *, code=None, inputs=None, **kwargs):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    script = work / "check.py"
    script.write_text(
        code or "from pathlib import Path\nassert Path('state').read_text() == 'pass'"
    )
    (work / "state").write_text("pass")
    job = receipt_jobs.register_job(
        db,
        profile_id="default",
        task_id="build",
        watcher_name="build",
        producer="checker",
        command=[sys.executable, str(script)],
        cwd=work,
        check_paths=[script],
        artifact_paths=inputs or ["state"],
        data_root=tmp_path / "slm",
        allow_prestart_evidence=True,
        **kwargs,
    )
    return work, script, job


def passes(db, job):
    return receipt_passes(
        SimpleNamespace(_db=db, profile_id="default"),
        [],
        {"job_id": job["job_id"]},
        "mcp_client",
        watcher_name="build",
    )


def test_definition_is_pinned_and_later_edits_do_not_change_check(receipt_db, tmp_path):
    work, script, job = register(receipt_db, tmp_path, code="raise SystemExit(1)")
    script.write_text("raise SystemExit(0)")
    assert receipt_jobs.run_registered_check(receipt_db, job["job_id"])["verdict"] == "FAIL"
    assert not passes(receipt_db, job)
    assert not list(work.glob("__pycache__"))


def test_rerun_binds_new_revision_and_old_receipt_cannot_finish(receipt_db, tmp_path):
    work, _, job = register(receipt_db, tmp_path)
    first = receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert passes(receipt_db, job)
    (work / "state").write_text("fail")
    assert not passes(receipt_db, job)
    second = receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert second["verdict"] == "FAIL"
    (work / "state").write_text("pass")
    assert not passes(receipt_db, job)  # newest failure still wins
    third = receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert passes(receipt_db, job)
    a, b, c = [json.loads(r["evidence"])["admission"] for r in (first, second, third)]
    assert a["revision_digest"] == c["revision_digest"] != b["revision_digest"]


def test_inputs_are_read_once_and_revision_hashes_copied_bytes(receipt_db, tmp_path, monkeypatch):
    work, _, job = register(receipt_db, tmp_path)
    from superlocalmemory.loops import artifact_revision

    original, seen = artifact_revision.copy_inputs, []

    def copied(*args, **kwargs):
        result = original(*args, **kwargs)
        seen.append(result)
        if len(seen) == 1:
            (work / "state").write_text("fail")
        return result

    monkeypatch.setattr(artifact_revision, "copy_inputs", copied)
    with pytest.raises(ValueError, match="changed during verification"):
        receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert not receipt_db.execute(
        "SELECT name FROM sqlite_master WHERE name='loop_execution_receipts'"
    )
    assert seen[0][0] == json.dumps(
        [
            {"path": "state", "sha256": hashlib.sha256(b"pass").hexdigest()},
        ],
        sort_keys=True,
    )


@pytest.mark.parametrize("replacement", ["file", "parent", "fifo"])
def test_symlinks_and_nonregular_inputs_are_refused(receipt_db, tmp_path, replacement):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state").write_text("pass")
    if replacement == "parent":
        (work / "data").symlink_to(outside, target_is_directory=True)
        inputs = ["data/state"]
    elif replacement == "file":
        (work / "link").symlink_to(outside / "state")
        inputs = ["link"]
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO fixture requires mkfifo")
        os.mkfifo(work / "pipe")
        inputs = ["pipe"]
    with pytest.raises(ValueError, match="regular|symlink|no-follow"):
        receipt_jobs.register_job(
            receipt_db,
            profile_id="default",
            task_id="build",
            watcher_name="build",
            producer="checker",
            command=[sys.executable, "-c", "pass"],
            cwd=work,
            artifact_paths=inputs,
            check_paths=[],
            data_root=tmp_path / "slm",
        )


def test_glob_membership_changes_invalidate_receipt(receipt_db, tmp_path):
    work, _, job = register(receipt_db, tmp_path, inputs=["s*"])
    receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert passes(receipt_db, job)
    (work / "second").write_text("new artifact")
    assert not passes(receipt_db, job)
    receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert passes(receipt_db, job)
    (work / "second").unlink()
    assert not passes(receipt_db, job)


def test_check_cannot_modify_its_copied_inputs_before_admission(receipt_db, tmp_path):
    _, _, job = register(
        receipt_db, tmp_path, code=("from pathlib import Path\nPath('state').write_text('fail')\n")
    )
    with pytest.raises(ValueError, match="copied inputs"):
        receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert not receipt_db.execute(
        "SELECT name FROM sqlite_master WHERE name='loop_execution_receipts'"
    )


def test_store_tampering_refuses_before_execution(receipt_db, tmp_path, monkeypatch):
    _, _, job = register(receipt_db, tmp_path)
    for path in (tmp_path / "slm").rglob("check.py"):
        path.write_text("pass")
    monkeypatch.setattr(
        execution_receipts, "_execution", lambda *a: pytest.fail("tampered check ran")
    )
    with pytest.raises(ValueError, match="definition"):
        receipt_jobs.run_registered_check(receipt_db, job["job_id"])


def test_execution_uses_private_dirs_allowlist_and_devnull(receipt_db, tmp_path, monkeypatch):
    from superlocalmemory.loops import check_process

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "malicious"))
    monkeypatch.setenv("SECRET_TEST_SENTINEL", "not a real secret")
    work, _, job = register(receipt_db, tmp_path, code="pass")
    real_run = check_process.subprocess.Popen
    captured = []

    def capture(command, **kwargs):
        captured.append((command, kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr(check_process.subprocess, "Popen", capture)
    receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    command, options = captured[0]
    run = Path(options["cwd"])
    assert not run.is_relative_to(work) and not run.exists()
    assert options["stdin"] == check_process.subprocess.DEVNULL
    assert "PYTHONPATH" not in options["env"] and "SECRET_TEST_SENTINEL" not in options["env"]
    for name in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert Path(options["env"][name]).is_relative_to(run)
    assert str(work) not in command[1]


def test_authenticated_receipt_rejects_changed_measurement(receipt_db, tmp_path):
    _, _, job = register(receipt_db, tmp_path)
    receipt = receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    from superlocalmemory.loops.execution_guard import verify_admission

    assert verify_admission(job, receipt)
    receipt["produced_at"] = "2026-01-01T00:00:00+00:00"
    assert not verify_admission(job, receipt)
    key = tmp_path / "slm" / "loop-admission" / "admission.key"
    assert key.stat().st_mode & 0o777 == 0o600
    for path in (tmp_path / "slm" / "loop-checks").rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
