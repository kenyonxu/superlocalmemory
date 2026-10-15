"""Independent fetched-head receipt admission probes; isolated review artifacts."""

import asyncio
import json
import sys
import threading

import pytest

from superlocalmemory.loops import (
    execution_guard as guard,
)
from superlocalmemory.loops import (
    execution_receipts as writer,
)
from superlocalmemory.loops import (
    private_workspace,
    receipt_jobs,
)
from tests.loops.hermetic_fixtures import private_data_root as private_data_root
from tests.mcp.test_loop_receipt_races import receipts
from tests.mcp.test_loop_receipt_replay import harness
from tests.mcp.test_loop_receipt_replay import receipt_db as receipt_db


def new_job(db, root, code="print('pass')", *, prior=True):
    script = root / "checker.py"
    script.write_text(code)
    (root / "state").write_text("pass")
    return receipt_jobs.register_job(
        db,
        profile_id="default",
        task_id="build",
        watcher_name="build",
        producer="checker",
        command=[sys.executable, "-I", str(script)],
        cwd=root,
        artifact_paths=["state"],
        check_paths=[script],
        allow_prestart_evidence=prior,
    )


def pause_stage(monkeypatch, stage, pause):
    if stage == "after_measurement":
        original = writer._execution

        def paused(*a, **kw):
            result = original(*a, **kw)
            pause()
            return result

        monkeypatch.setattr(writer, "_execution", paused)
    elif stage == "before_validation":
        original = receipt_jobs._validate_execution

        def paused(*a, **kw):
            pause()
            return original(*a, **kw)

        monkeypatch.setattr(receipt_jobs, "_validate_execution", paused)
    else:
        original = writer._store

        def paused(*a, **kw):
            pause()
            return original(*a, **kw)

        monkeypatch.setattr(writer, "_store", paused)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["after_measurement", "before_validation", "after_validation_before_insert"]
)
async def test_no_pending_pass_can_be_promoted(receipt_db, tmp_path, monkeypatch, stage):
    job = new_job(receipt_db, tmp_path)
    entered, release = threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert release.wait(5)

    pause_stage(monkeypatch, stage, pause)
    task = asyncio.create_task(
        asyncio.to_thread(receipt_jobs.run_registered_check, receipt_db, job["job_id"])
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert not receipts(receipt_db)
        out = await harness(receipt_db)(
            "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]}
        )
        assert out["status"] == "HALT" and not out["passed"]
    finally:
        release.set()
        receipt = await task
    assert receipt["verdict"] == "PASS"
    out = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]}
    )
    assert out["status"] == "DONE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof", [None, {}, "text", {"job_id": "0" * 32}, "wrong_revision", "old_version", "other_job"]
)
async def test_historical_proofs_fail_closed(receipt_db, tmp_path, proof):
    job = new_job(receipt_db, tmp_path)
    if isinstance(proof, str) and proof in {"wrong_revision", "old_version", "other_job"}:
        kind, proof = proof, guard.admission(job)
        if kind == "wrong_revision":
            proof["revision_digest"] = "0" * 64
        elif kind == "old_version":
            proof["validation"] = "prepost-sha256-v0"
        else:
            proof["job_id"] = "0" * 32
    # Trusted-writer fixture seeds migration/tampering cases; this is not an
    # assertion that an MCP caller can supply the callback.
    receipt = writer.run_check(
        receipt_db,
        profile_id=job["profile_id"],
        task_id=job["task_id"],
        run_id=job["run_id"],
        producer=job["producer"],
        command=json.loads(job["command_json"]),
        cwd=job["cwd"],
        validate_result=lambda: proof,
    )
    assert receipt["verdict"] == "PASS"
    out = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]}
    )
    assert out["status"] == "HALT" and not out["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["change", "supersede"])
async def test_mutation_after_validation_before_publication_is_not_usable(
    receipt_db, tmp_path, monkeypatch, action
):
    job = new_job(receipt_db, tmp_path)
    store = writer._store

    def interleave(db, receipt):
        if action == "change":
            (tmp_path / "state").write_text("fail")
        else:
            new_job(db, tmp_path)
        return store(db, receipt)

    monkeypatch.setattr(writer, "_store", interleave)
    receipt = receipt_jobs.run_registered_check(receipt_db, job["job_id"])
    assert receipt["verdict"] == "PASS"
    out = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": job["job_id"]}
    )
    assert out["status"] == "HALT" and not out["passed"]


@pytest.mark.asyncio
async def test_unsupported_platform_refuses_before_execution(receipt_db, tmp_path, monkeypatch):
    monkeypatch.setattr(private_workspace.os, "supports_dir_fd", set())
    monkeypatch.setattr(writer, "_execution", lambda *a: pytest.fail("unsupported check ran"))
    with pytest.raises(ValueError, match="unsupported.*no-follow"):
        new_job(receipt_db, tmp_path)
    assert not receipts(receipt_db)
    out = await harness(receipt_db)(
        "build", "build", max_iterations=1, gate_contract={"job_id": "0" * 32}
    )
    assert out["status"] == "HALT" and "no-follow" in out["reason"]


def test_unrelated_ancestor_sibling_change_rejects_valid_check(receipt_db, tmp_path, monkeypatch):
    """Record conservative availability regression without weakening safety."""
    work = tmp_path / "work"
    work.mkdir()
    sibling = tmp_path / "unrelated-process-output"
    job = new_job(
        receipt_db,
        work,
        f"from pathlib import Path\nPath({str(sibling)!r}).mkdir()\nprint('real check passed')",
    )
    script = work / "checker.py"
    before = script.read_bytes()
    measured = []
    execute = writer._execution

    def capture(*a, **kw):
        result = execute(*a, **kw)
        measured.append(result)
        return result

    monkeypatch.setattr(writer, "_execution", capture)
    assert receipt_jobs.run_registered_check(receipt_db, job["job_id"])["verdict"] == "PASS"
    assert measured[0][0] == 0 and "real check passed" in measured[0][1]
    assert script.read_bytes() == before
    assert len(receipts(receipt_db)) == 1


def test_preexisting_separate_output_directory_is_supported(receipt_db, tmp_path):
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    out.mkdir()
    job = new_job(
        receipt_db,
        work,
        f"from pathlib import Path\nPath({str(out / 'result')!r}).write_text('ok')",
    )
    assert receipt_jobs.run_registered_check(receipt_db, job["job_id"])["verdict"] == "PASS"
