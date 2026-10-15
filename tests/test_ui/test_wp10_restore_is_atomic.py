"""A dashboard restore holds the store for its whole run and is all-or-nothing.

PR 251 review, finding 4: the restore worker stopped the daemon but never took
the writer lease, so a hook or CLI call could start a fresh daemon that wrote
during the restore. The databases were restored atomically, but the settings
merge and the media copy ran after them: a failure there left restored
databases, partial settings and a stopped service, and nothing could put the
safety copy back.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
from argparse import Namespace

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def seed(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")
    (root / "config.json").write_text(json.dumps({"mode": "a", "scope": {"x": 1}}))


def change(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("UPDATE marker SET value='after'")
    (root / "config.json").write_text(json.dumps({"mode": "a", "scope": {"x": 2}}))


def markers(root):
    values = set()
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            values.add(conn.execute("SELECT value FROM marker").fetchone()[0])
    return values


def outcome(root):
    return json.loads((root / "backup-recovery-outcome.json").read_text())


@pytest.fixture
def worker(monkeypatch):
    module = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(module, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(module, "wait_stopped", lambda pid: calls.append("stopped"))
    module.calls = calls
    return module


def test_the_writer_lease_is_held_for_the_whole_restore(tmp_path, worker, monkeypatch):
    from superlocalmemory.core.file_lock import LockHeldError, exclusive_lock
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    lease = tmp_path / "memory.db.writer.lock"
    seen = {}
    real = backup_bundle.restore_point

    def checked(root, point_id):
        with pytest.raises(LockHeldError):
            with exclusive_lock(lease):
                pass
        seen["holder"] = json.loads(lease.read_text())["pid"]
        return real(root, point_id)

    monkeypatch.setattr(backup_bundle, "restore_point", checked)
    worker.run(tmp_path, point["id"], 123)
    assert seen["holder"] == os.getpid()
    assert worker.calls == ["stop", "stopped", "start"]
    assert outcome(tmp_path)["state"] == "complete"
    with exclusive_lock(lease):  # released before the restart
        pass


def test_a_daemon_start_is_refused_while_a_restore_runs(tmp_path):
    from superlocalmemory.server import recovery_marker
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    path = tmp_path / "backup-recovery-outcome.json"
    path.write_text(json.dumps({"state": "restoring", "worker_pid": os.getpid(),
                                "worker_identity": recovery_marker.identity(os.getpid())}))
    assert "restore" in restore_in_progress(tmp_path)
    path.write_text(json.dumps({"state": "complete", "worker_pid": os.getpid()}))
    assert restore_in_progress(tmp_path) is None


def test_the_daemon_start_path_asks_the_guard():
    import inspect

    from superlocalmemory.server import unified_daemon

    assert "restore_in_progress(" in inspect.getsource(unified_daemon)


def test_a_settings_failure_rolls_everything_back_and_restarts(tmp_path, worker, monkeypatch):
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    change(tmp_path)
    config_before = (tmp_path / "config.json").read_bytes()

    def broken(*args, **kwargs):
        raise OSError("disk full while writing settings")

    monkeypatch.setattr(backup_bundle, "update_config", broken)
    worker.run(tmp_path, point["id"], 123)

    assert markers(tmp_path) == {"after"}
    assert (tmp_path / "config.json").read_bytes() == config_before
    result = outcome(tmp_path)
    assert result["state"] == "failed"
    assert "nothing was changed" in result["message"].lower()
    assert worker.calls == ["stop", "stopped", "start"]


def test_a_failed_rollback_keeps_the_service_stopped_and_names_the_command(
    tmp_path, worker, monkeypatch
):
    from superlocalmemory.server import backup_bundle, backup_rollback

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("store unreadable")

    # Both the restore and the plain-file rollback fail (disk gone, say).
    monkeypatch.setattr(backup_bundle, "restore_point", broken)
    monkeypatch.setattr(backup_rollback.PreRestoreCopy, "roll_back", broken)
    worker.run(tmp_path, point["id"], 123)
    result = outcome(tmp_path)
    assert result["state"] == "failed"
    assert result["safety_id"]
    assert list((tmp_path / "backups").glob(".pre-restore-*")), "kept for manual recovery"
    assert "slm backup restore-safety-copy" in result["message"]
    assert worker.calls == ["stop", "stopped"]


def test_the_cli_puts_the_safety_copy_back(tmp_path, monkeypatch, capsys):
    from superlocalmemory.cli import backup_cmd
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    safety = backup_bundle.create_point(tmp_path)
    change(tmp_path)
    (tmp_path / "backup-recovery-outcome.json").write_text(
        json.dumps({"state": "failed", "safety_id": safety["id"]})
    )
    monkeypatch.setattr(backup_cmd, "_data_root", lambda: tmp_path)
    backup_cmd.cmd_backup(Namespace(backup_command="restore-safety-copy", id=None, json=True))
    printed = json.loads(capsys.readouterr().out)
    assert printed["restored"] == safety["id"]
    assert markers(tmp_path) == {"before"}


def test_the_cli_refuses_while_the_store_is_open(tmp_path, monkeypatch, capsys):
    from superlocalmemory.cli import backup_cmd
    from superlocalmemory.core.file_lock import exclusive_lock
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    safety = backup_bundle.create_point(tmp_path)
    change(tmp_path)
    monkeypatch.setattr(backup_cmd, "_data_root", lambda: tmp_path)
    with exclusive_lock(tmp_path / "memory.db.writer.lock"), pytest.raises(SystemExit):
        backup_cmd.cmd_backup(
            Namespace(backup_command="restore-safety-copy", id=safety["id"], json=True)
        )
    assert markers(tmp_path) == {"after"}


def test_the_cli_parser_accepts_the_command():
    import argparse

    from superlocalmemory.cli.backup_cmd import add_backup_parser

    parser = argparse.ArgumentParser()
    add_backup_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["backup", "restore-safety-copy", "--id", "0123456789abcdef"])
    assert args.backup_command == "restore-safety-copy" and args.id == "0123456789abcdef"
