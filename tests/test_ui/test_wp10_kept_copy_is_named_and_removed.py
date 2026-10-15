"""The pre-restore copy kept after a failed rollback is named, then removed.

PR 251 round 4, R3: after a failed rollback the full copy of every store was
kept for manual recovery, but no message said where, and nothing ever removed
it, not even after the stores had been recovered.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def seed(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")


@pytest.fixture
def failed_rollback(tmp_path, monkeypatch):
    from superlocalmemory.server import backup_bundle, backup_rollback

    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    monkeypatch.setattr(worker, "control", lambda root, action: None)
    monkeypatch.setattr(worker, "wait_stopped", lambda pid: None)
    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk gone")

    with monkeypatch.context() as patch:
        patch.setattr(backup_bundle, "restore_point", broken)
        patch.setattr(backup_rollback.PreRestoreCopy, "roll_back", broken)
        worker.run(tmp_path, point["id"], 123)
    kept = list((tmp_path / "backups").glob(".pre-restore-*"))
    assert len(kept) == 1
    return tmp_path, kept[0]


def test_the_outcome_and_the_start_refusal_name_the_kept_copy(failed_rollback):
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    root, kept = failed_rollback
    message = json.loads((root / "backup-recovery-outcome.json").read_text())["message"]
    assert str(kept) in message
    assert str(kept) in restore_in_progress(root)


def test_backup_status_shows_the_recovery_need_and_the_copy(failed_rollback, monkeypatch,
                                                            capsys):
    from superlocalmemory.cli import backup_cmd

    root, kept = failed_rollback
    monkeypatch.setattr(backup_cmd, "_data_root", lambda: root)
    backup_cmd.cmd_backup(Namespace(backup_command="status", json=False))
    text = capsys.readouterr().out
    assert "slm backup restore-safety-copy" in text and str(kept) in text
    backup_cmd.cmd_backup(Namespace(backup_command="status", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["recovery"]["kept_copy"] == str(kept)


def test_backup_status_is_quiet_when_nothing_is_needed(tmp_path, monkeypatch, capsys):
    from superlocalmemory.cli import backup_cmd

    monkeypatch.setattr(backup_cmd, "_data_root", lambda: tmp_path)
    backup_cmd.cmd_backup(Namespace(backup_command="status", json=True))
    assert json.loads(capsys.readouterr().out)["recovery"] is None


def test_restore_safety_copy_removes_the_kept_copy(failed_rollback):
    from superlocalmemory.server.backup_restore_txn import recover_safety_copy

    root, kept = failed_rollback
    safety = json.loads((root / "backup-recovery-outcome.json").read_text())["safety_id"]
    recover_safety_copy(root, safety)
    assert not kept.exists()
    assert not list((root / "backups").glob(".pre-restore-*"))


def test_clear_recovery_needed_removes_the_kept_copy(failed_rollback, monkeypatch):
    from superlocalmemory.cli import backup_cmd

    root, kept = failed_rollback
    monkeypatch.setattr(backup_cmd, "_data_root", lambda: root)
    backup_cmd.cmd_backup(Namespace(backup_command="clear-recovery-needed", json=False))
    assert not Path(kept).exists()
