"""A restore writer is identified by its pid AND its process start, not the pid alone.

PR 251 round 5, S1: after a crash or reboot the dead writer's pid can belong
to another live process. The marker then read as "a restore is replacing the
store", every start said "start again when it finishes", and nothing named
the recovery command.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from superlocalmemory.server import recovery_marker

COMMAND = "slm backup restore-safety-copy"


def _rewrite_pid(root, pid):
    path = root / recovery_marker.NAME
    marker = json.loads(path.read_text())
    marker["worker_pid"] = pid
    path.write_text(json.dumps(marker))


@pytest.fixture
def unrelated_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield child.pid
    child.kill()
    child.wait(timeout=30)


def test_a_reused_pid_reads_as_a_dead_writer(tmp_path, unrelated_process):
    recovery_marker.begin(tmp_path, "0" * 16)
    _rewrite_pid(tmp_path, unrelated_process)
    message = recovery_marker.refusal(tmp_path)
    assert COMMAND in message
    assert "replacing" not in message


def test_pid_one_reads_as_a_dead_writer(tmp_path):
    recovery_marker.begin(tmp_path, "0" * 16)
    _rewrite_pid(tmp_path, 1)
    assert COMMAND in recovery_marker.refusal(tmp_path)


def test_the_real_writer_still_reads_as_running(tmp_path):
    recovery_marker.begin(tmp_path, "0" * 16)
    message = recovery_marker.refusal(tmp_path)
    assert "replacing" in message
    assert f"If no restore is running, run `{COMMAND}`" in message


def test_a_marker_without_identity_falls_back_to_the_pid(tmp_path, unrelated_process):
    """Markers written before this change carry only a pid; they keep working."""
    (tmp_path / recovery_marker.NAME).write_text(json.dumps(
        {"phase": "writing", "worker_pid": unrelated_process}))
    assert "replacing" in recovery_marker.refusal(tmp_path)
