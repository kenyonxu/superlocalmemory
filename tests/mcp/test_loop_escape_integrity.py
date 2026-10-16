"""PR-247 recheck 3, part 2: torn copy, ancestor config variants, cross-job survivor, history."""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.loops.ledger import InMemoryLedger
from superlocalmemory.loops.receipt_jobs import register_job, run_registered_check
from superlocalmemory.mcp import loop_runner, tools_loops
from superlocalmemory.mcp.tools_loops import register_loop_tools
from tests.mcp.test_loop_independent_receipts import _threaded_receipt_connection
from tests.mcp.test_loop_tools import _Capture, _FakeEngine

PY = sys.executable



def record(**kw):
    print("PROBE", json.dumps(kw, default=str))


@pytest.fixture
def db():
    conn = _threaded_receipt_connection()
    yield SimpleNamespace(execute=lambda sql, p=(): conn.execute(sql, p).fetchall())
    conn.close()


@pytest.fixture
def ledger(monkeypatch):
    shared = InMemoryLedger()
    monkeypatch.setattr(loop_runner, "engine_backed_ledger", lambda *a, **k: shared)
    monkeypatch.setattr(tools_loops, "engine_backed_ledger", lambda *a, **k: shared)
    return shared


def tools(db):
    engine = _FakeEngine()
    engine._db = db
    engine.recall = lambda *a, **k: SimpleNamespace(
        results=[{"score": .99, "content": "Build FAILED; deployment BLOCKED"}],
        no_confident_match=False)
    server = _Capture()
    register_loop_tools(server, lambda: engine)
    return server.fns


async def watch(db, job_id, name="build"):
    return await tools(db)["slm_loop_run"](name, "build", max_iterations=1,
                                           gate_contract={"job_id": job_id})


def register(db, cwd, command, checks, artifacts, droot, watcher="build"):
    return register_job(
        db, profile_id="default", task_id="build", watcher_name=watcher, producer="checker",
        command=command, cwd=cwd, check_paths=checks, artifact_paths=artifacts,
        data_root=droot, allow_prestart_evidence=True)


def run(db, job):
    try:
        r = run_registered_check(db, job["job_id"])
        return {"verdict": r["verdict"], "exit": r["exit_code"]}
    except ValueError as error:
        return {"refused": str(error)}


DAEMON = r"""import os, sys, time
from pathlib import Path
me = Path(os.getcwd())
runs = me.parent
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        nul = os.open(os.devnull, os.O_RDWR)
        for f in (0, 1, 2):
            os.dup2(nul, f)
        seen = {me}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for d in runs.glob('slm-check-*'):
                t = d / 'state'
                if d in seen or not t.exists() or t.stat().st_size != 4:
                    continue
                seen.add(d)
                time.sleep(.05)
                t.write_text('pass')
                time.sleep(.6)
                t.write_text('fail')
                os._exit(0)
            time.sleep(.002)
        os._exit(0)
    os._exit(0)
os.wait()
raise SystemExit(0)
"""


async def test_N8_survivor_from_one_check_certifies_another_job(db, tmp_path):
    """Agent code executed by job X's check leaves a setsid survivor that flips job Y's
    private copy during Y's check and restores it before validation. Y's check never
    executes agent code."""
    droot = tmp_path / "slm"
    x = tmp_path / "projx"
    x.mkdir()
    (x / "test_runner.py").write_text("import agent_code\n")
    (x / "agent_code.py").write_text(DAEMON)  # mutable artifact the agent controls
    jx = register(db, x, [PY, "test_runner.py"], ["test_runner.py"], ["agent_code.py"], droot,
                  watcher="X")
    y = tmp_path / "projy"
    y.mkdir()
    (y / "state").write_text("fail")
    (y / "check.py").write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(.25)\n"
        "v = Path('state').read_text()\ntime.sleep(.8)\n"
        "raise SystemExit(0 if v == 'pass' else 1)\n")
    jy = register(db, y, [PY, "-I", "check.py"], ["check.py"], ["state"], droot, watcher="Y")
    honest_y = run(db, jy)
    rx = run(db, jx)
    time.sleep(.1)
    attacked_y = run(db, jy)
    result = await watch(db, jy["job_id"], name="Y")
    record(probe="N8_survivor_cross_job_private_copy_aba", honest_y=honest_y, x_run=rx,
           attacked_y=attacked_y, y_watcher=result["status"], y_reason=result["receipt_reason"],
           live_y_state=(y / "state").read_text())
    assert result["status"] != "DONE"
    assert rx["verdict"] == "FAIL"
    escaped = await watch(db, jx["job_id"], name="X")
    assert "a process started by the check is still running:" in escaped["receipt_reason"]
