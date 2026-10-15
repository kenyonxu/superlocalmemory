"""A rollback never repeats the step that failed.

PR 251 re-review N1: the rollback re-ran ``restore_point`` against the safety
point. A failure that repeats (an unreadable media catalog, a store write that
keeps failing) then failed the rollback too: the databases went one way, the
settings stayed merged from the backup, and the service stayed down.

Now the rollback is a plain file copy taken just before the restore: the
stores (with their -wal/-shm files), the vector index folder, the settings
files and the list of media originals. Putting them back runs no restore code.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def seed(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")
    (root / "config.json").write_text(json.dumps({"mode": "a", "scope": {"x": 1}}))
    (root / "adapters.json").write_text(json.dumps({"transcript": {"enabled": False}}))
    (root / "media").mkdir()
    with sqlite3.connect(root / "media" / "uploads.db") as conn:
        conn.execute("CREATE TABLE upload_links(id TEXT)")
        conn.execute("INSERT INTO upload_links VALUES('live-link')")


def change(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("UPDATE marker SET value='after'")
    (root / "config.json").write_text(json.dumps({"mode": "a", "scope": {"x": 2}}))
    (root / "lance").mkdir()
    (root / "lance" / "index.bin").write_bytes(b"live-index")


def markers(root):
    values = set()
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            values.add(conn.execute("SELECT value FROM marker").fetchone()[0])
    return values


def files(root):
    # The erasure ledger records what no restore may undo; it is never rolled back.
    skip = ("backups/", "logs/", "backup-recovery-outcome.json", "backup_obligations.db")
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not p.relative_to(root).as_posix().startswith(skip)
            and not p.name.endswith((".lock", "-wal", "-shm")) and p.name not in STORES}


@pytest.fixture
def worker(monkeypatch):
    module = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(module, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(module, "wait_stopped", lambda pid: calls.append("stopped"))
    module.calls = calls
    return module


def _outcome(root):
    return json.loads((root / "backup-recovery-outcome.json").read_text())


@pytest.mark.parametrize("step", ["_restore_surviving_originals", "carry_forward"])
def test_a_failure_that_repeats_still_rolls_back_everything(tmp_path, worker, monkeypatch, step):
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    change(tmp_path)
    before = files(tmp_path)

    def always(*args, **kwargs):
        raise ValueError("Media recovery refused: its catalog could not be read")

    monkeypatch.setattr(backup_bundle, step, always)
    worker.run(tmp_path, point["id"], 123)

    result = _outcome(tmp_path)
    assert result["state"] == "failed", result
    assert "nothing was changed" in result["message"].lower()
    assert markers(tmp_path) == {"after"}
    assert files(tmp_path) == before
    assert worker.calls == ["stop", "stopped", "start"]
    assert not list((tmp_path / "backups").glob(".pre-restore-*"))


def test_the_rollback_runs_no_restore_code(tmp_path, monkeypatch):
    from superlocalmemory.server import backup_bundle
    from superlocalmemory.server.backup_restore_txn import RolledBack, restore_atomically

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    change(tmp_path)
    calls = []
    real = backup_bundle.restore_point

    def once_then_fail(root, point_id, **kwargs):
        calls.append(point_id)
        real(root, point_id, **kwargs)
        raise OSError("disk full after the stores were written")

    monkeypatch.setattr(backup_bundle, "restore_point", once_then_fail)
    with pytest.raises(RolledBack):
        restore_atomically(tmp_path, point["id"])
    assert calls == [point["id"]]
    assert markers(tmp_path) == {"after"}
