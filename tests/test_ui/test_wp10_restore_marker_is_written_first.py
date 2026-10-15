"""A restore that dies at any point leaves start blocked, with no status read.

PR 251 round 4, R1: the recovery-needed marker was written only by the
dashboard's status check, which runs inside the daemon that a restore has
stopped. A worker killed while restoring (crash, power loss, OOM) left no
marker, so the next start ran migrations over half-restored stores.

Now the marker is written before the first destructive write and removed only
after success or a clean rollback.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")

_HANG = r"""
import sys, time
from pathlib import Path
from superlocalmemory.server import backup_bundle
from superlocalmemory.server.backup_restore_txn import restore_atomically

def hang(*args, **kwargs):
    print("WRITING", flush=True)
    time.sleep(600)

backup_bundle.restore_point = hang
restore_atomically(Path(sys.argv[1]), sys.argv[2], safety_id=sys.argv[2])
"""


def seed(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
            conn.execute("INSERT INTO marker VALUES('before')")


@pytest.fixture
def killed_mid_restore(tmp_path):
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", _HANG, str(tmp_path), point["id"]],
                             stdout=subprocess.PIPE, text=True,
                             env={**os.environ, "SLM_DATA_DIR": str(tmp_path)})
    try:
        assert child.stdout.readline().strip() == "WRITING"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=30)
    return tmp_path


def test_a_restore_killed_while_writing_blocks_start_without_a_status_read(killed_mid_restore):
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    message = restore_in_progress(killed_mid_restore)
    assert message and "slm backup restore-safety-copy" in message


def test_every_start_path_refuses_after_the_kill(killed_mid_restore, monkeypatch, capsys):
    from argparse import Namespace

    from superlocalmemory.cli import commands, daemon

    monkeypatch.setattr(daemon, "is_daemon_running", lambda: False)
    monkeypatch.setattr(daemon, "_recovery_root", lambda: killed_mid_restore)
    monkeypatch.setattr(daemon, "_start_daemon_subprocess",
                        lambda *a, **k: pytest.fail("spawned on a mixed store"))
    monkeypatch.setenv("SLM_TEST_ALLOW_DAEMON_SPAWN", "1")
    assert daemon.ensure_daemon() is False
    with pytest.raises(SystemExit):
        commands.cmd_serve(Namespace(action="start"))
    assert "slm backup restore-safety-copy" in capsys.readouterr().out


def test_a_live_restore_reads_as_in_progress_not_as_broken(tmp_path):
    from superlocalmemory.server import recovery_marker
    from superlocalmemory.server.backup_restore_txn import restore_in_progress

    recovery_marker.begin(tmp_path, "0" * 16)
    assert "replacing" in restore_in_progress(tmp_path)


def test_success_and_a_clean_rollback_both_remove_the_marker(tmp_path, monkeypatch):
    from superlocalmemory.server import backup_bundle
    from superlocalmemory.server.backup_restore_txn import (
        RolledBack,
        restore_atomically,
        restore_in_progress,
    )

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)["id"]
    restore_atomically(tmp_path, point, safety_id=point)
    assert restore_in_progress(tmp_path) is None

    def broken(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(backup_bundle, "restore_point", broken)
    with pytest.raises(RolledBack):
        restore_atomically(tmp_path, point, safety_id=point)
    assert restore_in_progress(tmp_path) is None
    assert not (tmp_path / "recovery-needed.json").exists()


def test_a_clean_rollback_keeps_an_earlier_recovery_need(tmp_path, monkeypatch):
    """restore-safety-copy that rolls back cleanly leaves the store as broken as before."""
    from superlocalmemory.server import backup_bundle, recovery_marker
    from superlocalmemory.server.backup_restore_txn import (
        RolledBack,
        restore_atomically,
        restore_in_progress,
    )

    seed(tmp_path)
    point = backup_bundle.create_point(tmp_path)["id"]
    recovery_marker.needs_recovery(tmp_path, "an earlier restore failed", point, None)

    def broken(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(backup_bundle, "restore_point", broken)
    with pytest.raises(RolledBack):
        restore_atomically(tmp_path, point, safety_id=point)
    assert "slm backup restore-safety-copy" in restore_in_progress(tmp_path)
    assert json.loads((tmp_path / "recovery-needed.json").read_text())["phase"] == \
        "needs_recovery"
