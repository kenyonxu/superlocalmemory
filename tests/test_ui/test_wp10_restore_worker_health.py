"""A restore worker that dies must not wedge recovery, and must leave its log.

PR 251 review, finding 6: the route refused every restore while the status
said queued, stopping or restoring. A worker that died (import error, kill,
reboot) never cleared it, so every later restore answered 409 for ever and
pruning kept protecting those points. Its output went to /dev/null.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DEAD_PID = 2**22 + 12345  # above any pid this test machine hands out


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server.routes import backup_recovery

    monkeypatch.setattr(backup_recovery, "_gate", lambda request: None)
    monkeypatch.setattr(backup_recovery, "_root", lambda: tmp_path)
    monkeypatch.setattr(backup_recovery, "_verify", lambda point_id: {})
    spawned = []
    monkeypatch.setattr(backup_recovery, "_spawn",
                        lambda root, point_id: spawned.append(point_id) or os.getpid())
    app = FastAPI()
    app.include_router(backup_recovery.router)
    client = TestClient(app)
    client.spawned = spawned
    return client


def write_status(root: Path, **status) -> None:
    (root / "backup-recovery-outcome.json").write_text(json.dumps(status))


def test_a_dead_worker_mid_restore_is_reported_and_releases_the_queue(api, tmp_path):
    write_status(tmp_path, state="restoring", id="0123456789abcdef",
                 safety_id="fedcba9876543210", worker_pid=DEAD_PID)
    status = api.get("/api/backup/recovery/status").json()
    assert status["state"] == "failed"
    assert "slm backup restore-safety-copy" in status["message"]
    assert "backup-recovery.log" in status["message"]
    assert status["safety_id"] == "fedcba9876543210"
    response = api.post("/api/backup/recovery/restore",
                        json={"id": "0123456789abcdef", "confirm": "RESTORE"})
    assert response.status_code == 200, response.text
    assert api.spawned == ["0123456789abcdef"]


def test_a_worker_that_never_started_changed_nothing(api, tmp_path):
    write_status(tmp_path, state="queued", id="0123456789abcdef", worker_pid=DEAD_PID)
    status = api.get("/api/backup/recovery/status").json()
    assert status["state"] == "failed"
    assert "nothing was changed" in status["message"].lower()


def test_a_live_worker_still_blocks_a_second_restore(api, tmp_path):
    from superlocalmemory.server import recovery_marker

    write_status(tmp_path, state="restoring", id="0123456789abcdef", worker_pid=os.getpid(),
                 worker_identity=recovery_marker.identity(os.getpid()))
    assert api.get("/api/backup/recovery/status").json()["state"] == "restoring"
    response = api.post("/api/backup/recovery/restore",
                        json={"id": "0123456789abcdef", "confirm": "RESTORE"})
    assert response.status_code == 409


def test_the_queued_status_records_the_worker(api, tmp_path):
    api.post("/api/backup/recovery/restore", json={"id": "0123456789abcdef", "confirm": "RESTORE"})
    status = json.loads((tmp_path / "backup-recovery-outcome.json").read_text())
    assert status["worker_pid"] == os.getpid()
    assert isinstance(status["worker_identity"], dict)


def test_the_worker_output_goes_to_a_log_file(tmp_path, monkeypatch):
    from superlocalmemory.server.routes import backup_recovery

    seen = {}

    class Fake:
        pid = 4242

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(backup_recovery.subprocess, "Popen", Fake)
    assert backup_recovery._spawn(tmp_path, "0123456789abcdef") == 4242
    assert seen["stdout"] not in (subprocess.DEVNULL, None)
    assert seen["stderr"] == subprocess.STDOUT
    assert (tmp_path / "logs" / "backup-recovery.log").exists()


def test_a_crashing_worker_leaves_its_reason_in_the_log(tmp_path):
    env = {**os.environ, "SLM_DATA_DIR": str(tmp_path), "HOME": str(tmp_path)}
    subprocess.run(
        [sys.executable, "-m", "superlocalmemory.server.backup_restore_worker",
         str(tmp_path), "0123456789abcdef", str(DEAD_PID)],
        env=env, timeout=120, check=False,
    )
    log = (tmp_path / "logs" / "backup-recovery.log").read_text()
    assert "Backup recovery failed" in log
    assert "verification failed" in log.lower()
