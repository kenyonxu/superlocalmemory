"""A restore checks free space first and never leaves partial copies behind.

PR 251 round 4, R2: a restore needs about three times the store size (the
safety point, the pre-write copy and the backup coordinator's own snapshot)
and nothing checked for it. On a full disk the message said "retry when other
activity has finished", and every retry left another partial
``backups/.pre-restore-*`` folder, filling the disk further.
"""

from __future__ import annotations

import errno
import importlib
import json
import sqlite3
from collections import namedtuple

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")
Usage = namedtuple("Usage", "total used free")


def seed(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")


def leftovers(root):
    return sorted(p.name for p in (root / "backups").glob(".pre-restore-*"))


@pytest.fixture
def worker(monkeypatch):
    module = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(module, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(module, "wait_stopped", lambda pid: calls.append("stopped"))
    module.calls = calls
    return module


def outcome(root):
    return json.loads((root / "backup-recovery-outcome.json").read_text())


def test_too_little_space_refuses_before_stopping_anything(tmp_path, worker, monkeypatch):
    from superlocalmemory.server import backup_bundle, backup_rollback

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    monkeypatch.setattr(backup_rollback.shutil, "disk_usage", lambda path: Usage(1, 1, 1024))
    worker.run(tmp_path, point["id"], 123)
    result = outcome(tmp_path)
    assert result["state"] == "failed"
    assert "MB free" in result["message"] and "available" in result["message"]
    assert "nothing was changed" in result["message"].lower()
    assert worker.calls == []
    assert leftovers(tmp_path) == []


def test_the_restore_route_refuses_up_front(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server import backup_bundle, backup_rollback
    from superlocalmemory.server.routes import backup_recovery

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    monkeypatch.setattr(backup_recovery, "_gate", lambda request: None)
    monkeypatch.setattr(backup_recovery, "_root", lambda: tmp_path)
    monkeypatch.setattr(backup_recovery, "_spawn", lambda *a: pytest.fail("spawned"))
    monkeypatch.setattr(backup_rollback.shutil, "disk_usage", lambda path: Usage(1, 1, 1024))
    app = FastAPI()
    app.include_router(backup_recovery.router)
    response = TestClient(app).post("/api/backup/recovery/restore",
                                    json={"id": point["id"], "confirm": "RESTORE"})
    assert response.status_code == 409
    assert "MB free" in response.json()["detail"]


def test_a_copy_that_fails_part_way_is_removed(tmp_path, worker, monkeypatch):
    from superlocalmemory.server import backup_bundle, backup_rollback

    seed(tmp_path)
    (tmp_path / "lance").mkdir()
    (tmp_path / "lance" / "index.bin").write_bytes(b"x")
    point = backup_bundle.create_point(tmp_path)

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backup_rollback.shutil, "copytree", full)
    worker.run(tmp_path, point["id"], 123)
    result = outcome(tmp_path)
    assert result["state"] == "failed"
    assert "disk is full" in result["message"].lower()
    assert leftovers(tmp_path) == []
    assert not (tmp_path / "recovery-needed.json").exists()


def test_stale_partial_copies_are_swept_at_the_next_start(tmp_path):
    from superlocalmemory.server import backup_rollback, recovery_marker

    stale = tmp_path / "backups" / ".pre-restore-1"
    kept = tmp_path / "backups" / ".pre-restore-2"
    for folder in (stale, kept):
        folder.mkdir(parents=True)
        (folder / "memory.db").write_bytes(b"partial")
    recovery_marker.needs_recovery(tmp_path, "rollback failed", None, str(kept))
    backup_rollback.sweep_stale(tmp_path)
    assert leftovers(tmp_path) == [".pre-restore-2"]


def test_daemon_start_up_sweeps_them(tmp_path):
    import inspect

    from superlocalmemory.server import unified_daemon

    assert "sweep_stale(" in inspect.getsource(unified_daemon)
