"""After a restore leaves a mixed store, nothing starts the service on it.

PR 251 re-review N3: after a failed rollback (or a worker that died while
restoring) the status said the service stays stopped, but the start-up guard
only refused while a live worker was restoring. Any hook or CLI call could
start the daemon on the mixed store and run migrations over it.

Now a durable "recovery needed" marker is written in those cases. Daemon
start-up and ensure_daemon refuse while it exists, naming the command; it is
cleared by a successful `slm backup restore-safety-copy`, or explicitly by
the owner with `slm backup clear-recovery-needed`.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from argparse import Namespace

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def seed(root, value="before"):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS marker(value TEXT)")
            conn.execute("DELETE FROM marker")
            conn.execute("INSERT INTO marker VALUES(?)", (value,))


@pytest.fixture
def failed_rollback(tmp_path, monkeypatch):
    from superlocalmemory.server import backup_bundle, backup_rollback

    worker = importlib.import_module("superlocalmemory.server.backup_restore_worker")
    calls = []
    monkeypatch.setattr(worker, "control", lambda root, action: calls.append(action))
    monkeypatch.setattr(worker, "wait_stopped", lambda pid: None)
    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk gone")

    with monkeypatch.context() as patch:
        patch.setattr(backup_bundle, "restore_point", broken)
        patch.setattr(backup_rollback.PreRestoreCopy, "roll_back", broken)
        worker.run(tmp_path, point["id"], 123)
    assert calls == ["stop"]
    return tmp_path


def test_a_failed_rollback_blocks_every_start(failed_rollback):
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    message = restore_in_progress(failed_rollback)
    assert message and "slm backup restore-safety-copy" in message


def test_a_worker_that_died_while_restoring_blocks_every_start(tmp_path):
    from superlocalmemory.server.backup_restore_txn import restore_in_progress
    from superlocalmemory.server.routes.backup_recovery import _settled

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "backup-recovery-outcome.json").write_text(json.dumps(
        {"state": "restoring", "id": "0" * 16, "safety_id": "1" * 16, "worker_pid": dead.pid}))
    assert _settled(tmp_path)["state"] == "failed"
    assert "slm backup restore-safety-copy" in restore_in_progress(tmp_path)


def test_a_worker_that_died_before_restoring_does_not_block(tmp_path):
    from superlocalmemory.server.backup_restore_txn import restore_in_progress
    from superlocalmemory.server.routes.backup_recovery import _settled

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "backup-recovery-outcome.json").write_text(json.dumps(
        {"state": "stopping", "id": "0" * 16, "worker_pid": dead.pid}))
    _settled(tmp_path)
    assert restore_in_progress(tmp_path) is None


def test_restore_safety_copy_clears_it(failed_rollback):
    from superlocalmemory.server.backup_restore_txn import recover_safety_copy, restore_in_progress

    safety = json.loads((failed_rollback / "backup-recovery-outcome.json").read_text())["safety_id"]
    recover_safety_copy(failed_rollback, safety)
    assert restore_in_progress(failed_rollback) is None


def test_the_owner_can_clear_it_explicitly(failed_rollback, monkeypatch, capsys):
    from superlocalmemory.cli import backup_cmd
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    monkeypatch.setattr(backup_cmd, "_data_root", lambda: failed_rollback)
    backup_cmd.cmd_backup(Namespace(backup_command="clear-recovery-needed", json=False))
    assert "cleared" in capsys.readouterr().out.lower()
    assert restore_in_progress(failed_rollback) is None


def test_ensure_daemon_does_not_spawn_while_recovery_is_needed(failed_rollback, monkeypatch):
    from superlocalmemory.cli import daemon

    monkeypatch.setattr(daemon, "is_daemon_running", lambda: False)
    monkeypatch.setattr(daemon, "_recovery_root", lambda: failed_rollback)
    monkeypatch.setattr(daemon, "_start_daemon_subprocess",
                        lambda *a, **k: pytest.fail("spawned on a mixed store"))
    monkeypatch.setenv("SLM_TEST_ALLOW_DAEMON_SPAWN", "1")
    assert daemon.ensure_daemon() is False


def test_the_cli_parser_accepts_clear_recovery_needed():
    import argparse

    from superlocalmemory.cli.backup_cmd import add_backup_parser

    parser = argparse.ArgumentParser()
    add_backup_parser(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["backup", "clear-recovery-needed"]).backup_command == \
        "clear-recovery-needed"
