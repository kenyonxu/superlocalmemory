"""The live-daemon watch must distinguish real invocations from shell text."""

from types import SimpleNamespace

import psutil

from tests.test_integration.test_per_request_profile_e2e import (
    _daemon_port,
    _foreign_daemon_pids,
)

MODULE = "superlocalmemory.server.unified_daemon"


def test_private_port_accepts_both_argparse_spellings():
    assert _daemon_port(["python", "-m", MODULE, "--start", "--port", "52100"]) == 52100
    assert _daemon_port(["python", "-m", MODULE, "--start", "--port=52100"]) == 52100


def test_only_actual_production_daemon_invocations_are_watched(monkeypatch):
    inventory = [
        (101, ["python", "-m", MODULE, "--start"]),
        (102, ["zsh", "-c", f"python -m {MODULE} --start --port=52100"]),
        (103, ["python", "-c", f"from {MODULE} import create_app"]),
        (104, ["python", "-m", MODULE, "--start", "--port", "52100"]),
        (105, ["python", "-m", MODULE, "--start", "--port=8767"]),
        (106, ["python", "/src/superlocalmemory/server/unified_daemon.py",
               "--start", "--port=8765"]),
        (107, ["python", "-u", "/src/superlocalmemory/server/unified_daemon.py",
               "--start", "--port=8765"]),
        (108, ["python", "-c", "pass", "-m", MODULE, "--start"]),
        (109, ["python", "-u", "-m", MODULE, "--start", "--port=8767"]),
        (110, ["python", "/tmp/other.py", "-m", MODULE, "--start"]),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *a: [
        SimpleNamespace(info={"pid": pid, "cmdline": args}) for pid, args in inventory])
    assert _foreign_daemon_pids() == {101, 105, 106, 107, 109}
