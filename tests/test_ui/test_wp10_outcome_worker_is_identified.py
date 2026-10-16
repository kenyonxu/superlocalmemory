"""The restore outcome file names its worker by pid AND process start.

PR 251 round 6, S1 residual: start_refusal and settle checked the outcome's
worker_pid with a bare os.kill(pid, 0), so after a crash or reboot an unrelated
live process holding that pid wedged every start as "restore_running" and kept
the dashboard on "stopping" for good.
"""

from __future__ import annotations

import json
import os

from superlocalmemory.server import backup_restore_txn as txn
from superlocalmemory.server import recovery_marker


def _write(root, **fields):
    (root / txn.OUTCOME_NAME).write_text(json.dumps(fields))


def test_an_unrelated_live_pid_without_identity_is_not_a_running_restore(tmp_path):
    _write(tmp_path, state="stopping", worker_pid=os.getppid())
    found = txn.start_refusal(tmp_path)
    assert found is None or found[0] != recovery_marker.RUNNING


def test_an_unrelated_live_pid_with_a_foreign_identity_is_not_running(tmp_path):
    foreign = {"token": "not-this-process-start", "created": 1.0}
    _write(tmp_path, state="stopping", worker_pid=os.getppid(), worker_identity=foreign)
    found = txn.start_refusal(tmp_path)
    assert found is None or found[0] != recovery_marker.RUNNING


def test_settle_moves_a_stopping_status_with_an_unrelated_pid_out_of_stopping(tmp_path):
    status = {"state": "stopping", "worker_pid": os.getppid()}
    settled = txn.settle(status, tmp_path)
    assert settled["state"] == "failed"


def test_a_genuine_live_worker_with_matching_identity_is_running(tmp_path):
    pid = os.getpid()
    _write(tmp_path, state="restoring", worker_pid=pid,
           worker_identity=recovery_marker.identity(pid))
    found = txn.start_refusal(tmp_path)
    assert found is not None and found[0] == recovery_marker.RUNNING


def test_settle_leaves_a_genuine_live_worker_alone(tmp_path):
    pid = os.getpid()
    status = {"state": "stopping", "worker_pid": pid,
              "worker_identity": recovery_marker.identity(pid)}
    assert txn.settle(status, tmp_path)["state"] == "stopping"


def test_a_dead_pid_is_dead_with_or_without_identity(tmp_path):
    assert not recovery_marker.writer_alive({"worker_pid": 2 ** 22 + 12345}, require_identity=True)


def test_the_worker_records_its_identity(tmp_path):
    from superlocalmemory.server.backup_restore_worker import _Outcome

    outcome = _Outcome(tmp_path, "pt")
    outcome.write("stopping")
    record = json.loads(outcome.path.read_text())
    assert record["worker_pid"] == os.getpid()
    assert isinstance(record["worker_identity"], dict)
    assert recovery_marker.writer_alive(record, require_identity=True)
