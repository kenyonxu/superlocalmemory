"""BRN-08: relevance/support cannot substitute for independent execution."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from superlocalmemory.mcp.tools_loops import register_loop_tools
from tests.loops.hermetic_fixtures import private_data_root as private_data_root
from tests.mcp.test_loop_tools import _Capture, _FakeEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("require_support", [False, True])
async def test_failed_related_memory_never_finishes(require_support):
    engine = _FakeEngine()
    engine.recall = lambda *a, **k: SimpleNamespace(
        results=[{"score": 0.99, "content": "Build FAILED. Deployment is BLOCKED."}],
        no_confident_match=False,
        answerability="supported",
    )
    server = _Capture()
    register_loop_tools(server, lambda: engine)
    result = await server.fns["slm_loop_run"](
        "failed", "build passed", max_iterations=1, require_support=require_support
    )
    assert result["status"] == "HALT"
    assert result["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["none", "self", "failed", "wrong_run", "stale", "digest", "unknown_author"]
)
async def test_only_exact_independent_receipt_can_finish(change, tmp_path):
    import sys

    from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check

    conn = _threaded_receipt_connection()
    db = SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    producer = "mcp_client" if change == "self" else "checker"
    command = [
        sys.executable,
        "-I",
        "-c",
        "raise SystemExit(1)" if change == "failed" else "print('6 passed')",
    ]
    artifact = tmp_path / "build"
    artifact.write_text("current revision")
    job = register_job(
        db,
        profile_id="default",
        task_id="build",
        watcher_name="gate",
        producer=producer,
        command=command,
        cwd=tmp_path,
        artifact_paths=[artifact],
        allow_prestart_evidence=True,
    )
    receipt = run_registered_check(db, job["job_id"])
    contract = _case_contract(receipt, change, job)
    engine = _FakeEngine()
    engine._db = db
    engine.recall = lambda *a, **k: SimpleNamespace(
        results=[{"fact_id": "locator", "score": 0.9}], no_confident_match=False
    )
    server = _Capture()
    register_loop_tools(server, lambda: engine)
    result = await server.fns["slm_loop_run"](
        "gate", "build", max_iterations=1, gate_contract=contract
    )
    assert result.get("status") == ("DONE" if change == "none" else "HALT"), result
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE loop_execution_receipts SET verdict='PASS'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM loop_execution_receipts")
    conn.close()


def test_ordinary_memory_cannot_mint_independent_evidence():
    from superlocalmemory.loops.receipt_gate import receipt_passes

    evidence = "claimed build passed"
    digest = hashlib.sha256(evidence.encode()).hexdigest()
    produced = datetime.now(UTC).isoformat()
    actor = "daemon-capability:local-writer"
    receipt = {
        "task_id": "build",
        "run_id": "run",
        "producer": actor,
        "verdict": "PASS",
        "exit_code": 0,
        "produced_at": produced,
        "evidence": evidence,
        "evidence_digest": digest,
    }
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT PRIMARY KEY); "
        "INSERT OR IGNORE INTO profiles VALUES ('default');"
    )
    conn.executescript(
        "CREATE TABLE atomic_facts (fact_id TEXT,profile_id TEXT,content TEXT);"
        "CREATE TABLE provenance (fact_id TEXT,profile_id TEXT,created_by TEXT,timestamp TEXT);"
    )
    conn.execute("INSERT INTO atomic_facts VALUES (?,?,?)", ("f", "default", json.dumps(receipt)))
    conn.execute("INSERT INTO provenance VALUES (?,?,?,?)", ("f", "default", actor, produced))
    engine = SimpleNamespace(
        profile_id="default",
        _db=SimpleNamespace(execute=lambda s, p=(): conn.execute(s, p).fetchall()),
    )
    contract = dict(
        task_id="build",
        run_id="run",
        producer=actor,
        min_timestamp="2026-01-01T00:00:00+00:00",
        evidence_digest=digest,
        command_digest="1" * 64,
    )
    assert not receipt_passes(engine, [{"fact_id": "f"}], contract, "mcp_client")
    conn.close()


def _case_contract(receipt, change, job=None):
    contract = {
        key: receipt[key]
        for key in ("task_id", "run_id", "producer", "command_digest", "evidence_digest")
    }
    contract["min_timestamp"] = "2026-01-01T00:00:00+00:00"
    if change == "wrong_run":
        contract["run_id"] = "different-run"
    elif change == "stale":
        contract["min_timestamp"] = "2100-01-01T00:00:00+00:00"
    elif change == "digest":
        contract["evidence_digest"] = "0" * 64
    elif change == "unknown_author":
        contract["producer"] = "unregistered-checker"
    if job is not None:
        if change in {"none", "self", "failed"}:
            return {"job_id": job["job_id"]}
        # The requester cannot override registered identity, time or evidence.
        return {"job_id": job["job_id"], **contract}
    return contract


def _threaded_receipt_connection():
    conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT PRIMARY KEY); "
        "INSERT OR IGNORE INTO profiles VALUES ('default');"
    )
    return conn


@pytest.mark.asyncio
async def test_waits_for_independent_receipt_with_unpredictable_output(tmp_path):
    import asyncio
    import sys

    from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check

    conn = _threaded_receipt_connection()
    db = SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    command = [sys.executable, "-I", "-c", "import uuid; print(uuid.uuid4())"]
    artifact = tmp_path / "build"
    artifact.write_text("current revision")
    job = register_job(
        db, profile_id="default", task_id="build", watcher_name="wait", producer="checker",
        command=command, cwd=tmp_path, artifact_paths=[artifact],
    )
    contract = {"job_id": job["job_id"]}
    engine = _FakeEngine()
    engine._db = db
    engine.recall = lambda *a, **k: SimpleNamespace(
        results=[{"score": 0.9}], no_confident_match=False
    )
    server = _Capture()
    register_loop_tools(server, lambda: engine)

    async def independent_check():
        await asyncio.sleep(0.1)
        return await asyncio.to_thread(
            run_registered_check, db, job["job_id"],
        )

    checker = asyncio.create_task(independent_check())
    # The check starts a fresh interpreter, which can take seconds on a loaded
    # machine. Give the loop a bounded window long enough not to race it.
    result = await server.fns["slm_loop_run"](
        "wait", "build", max_iterations=40, poll_interval_s=0.25, gate_contract=contract
    )
    await checker
    assert result["status"] == "DONE", result
    assert result["laps"] >= 2
    conn.close()


def test_latest_failed_execution_overrides_an_older_pass(tmp_path):
    import sys

    from superlocalmemory.loops.execution_receipts import run_check
    from superlocalmemory.loops.receipt_gate import receipt_passes
    from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT PRIMARY KEY); "
        "INSERT OR IGNORE INTO profiles VALUES ('default');"
    )
    db = SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    state = tmp_path / "state"
    command = [
        sys.executable,
        "-I",
        "-c",
        "from pathlib import Path; raise SystemExit(0 if Path('state').read_text()=='pass' else 1)",
    ]
    state.write_text("pass")
    job = register_job(
        db, profile_id="default", task_id="build", watcher_name="build", producer="checker",
        command=command, cwd=tmp_path, artifact_paths=[state], allow_prestart_evidence=True,
    )
    run_registered_check(db, job["job_id"])
    contract = {"job_id": job["job_id"]}
    engine = SimpleNamespace(_db=db, profile_id="default")
    assert receipt_passes(engine, [], contract, "mcp_client", watcher_name="build")
    state.write_text("fail")
    run_check(
        db,
        profile_id="default",
        task_id="build",
        run_id=job["run_id"],
        producer="checker",
        command=command,
        cwd=tmp_path,
    )
    state.write_text("pass")  # Same current artifact; newest real execution still failed.
    assert not receipt_passes(
        engine, [], contract, "mcp_client", watcher_name="build",
    )
    conn.close()


def test_receipt_erasure_follows_its_profile(tmp_path):
    import sys

    from superlocalmemory.loops.execution_receipts import run_check

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT PRIMARY KEY); "
        "INSERT OR IGNORE INTO profiles VALUES ('default');"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    db = SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    run_check(
        db,
        profile_id="default",
        task_id="check",
        run_id="erase",
        producer="checker",
        command=[sys.executable, "-I", "-c", "pass"],
        cwd=tmp_path,
    )
    conn.execute("DELETE FROM profiles WHERE profile_id='default'")
    assert conn.execute("SELECT COUNT(*) FROM loop_execution_receipts").fetchone()[0] == 0
    conn.close()
