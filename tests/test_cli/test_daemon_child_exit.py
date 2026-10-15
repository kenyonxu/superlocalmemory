"""A failed daemon child must release the CLI promptly, with a useful reason."""

from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.cli import daemon
from superlocalmemory.infra.daemon_identity import build_descriptor


@pytest.fixture
def spawn(monkeypatch):
    child = MagicMock()
    child.poll.return_value = 1
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: child)
    monkeypatch.setattr(daemon, "is_daemon_running", lambda: False)
    monkeypatch.setattr(daemon, "_has_tcp_listener", lambda port: False)
    monkeypatch.setattr(daemon, "_verified_legacy_health", lambda: None)
    monkeypatch.setattr(daemon, "read_descriptor", lambda: None)
    sleeps = []
    monkeypatch.setattr(daemon.time, "sleep", sleeps.append)
    return child, sleeps


@pytest.mark.parametrize("stale", [False, True])
def test_dead_child_fails_without_spending_start_budget(spawn, monkeypatch, stale, free_port):
    child, sleeps = spawn
    if stale:
        record = build_descriptor(port=free_port, version="4.1.25")
        monkeypatch.setattr(daemon, "read_descriptor", lambda: record)
        monkeypatch.setattr(daemon, "_descriptor_process_is_alive", lambda d: False)
    assert daemon._start_daemon_subprocess(port=free_port) is False
    assert len(sleeps) <= 1, "dead child consumed the full startup budget"
    child.poll.assert_called()


def test_alive_child_keeps_full_start_budget(spawn, free_port):
    child, sleeps = spawn
    child.poll.return_value = None
    assert daemon._start_daemon_subprocess(port=free_port) is False
    assert sum(sleeps) == 60


@pytest.mark.parametrize("health_delay", [0, 2])
def test_losing_child_honours_a_live_race_winner(spawn, monkeypatch, free_port, health_delay):
    _, sleeps = spawn
    record = build_descriptor(port=free_port, version="4.1.25")
    # Publish during poll(): the first descriptor read can precede the winner.
    def exited():
        monkeypatch.setattr(daemon, "read_descriptor", lambda: record)
        return 1

    spawn[0].poll.side_effect = exited
    monkeypatch.setattr(daemon, "_descriptor_process_is_alive", lambda d: True)
    health = MagicMock(side_effect=[None] * health_delay + [record.public_health_fields()])
    monkeypatch.setattr(daemon, "_fetch_health", health)
    assert daemon._start_daemon_subprocess(port=free_port) is True
    assert len(sleeps) <= health_delay + 2


def test_failed_start_names_log_and_current_error(spawn, tmp_path, free_port, caplog):
    log = tmp_path / "logs" / "daemon.log"
    log.parent.mkdir()
    log.write_text("ERROR old unrelated failure\n")

    def launch(*a, **k):
        k["stderr"].write("Booting\nERROR database could not open\nERROR later failure\n")
        k["stderr"].flush()
        return spawn[0]

    with patch("subprocess.Popen", side_effect=launch):
        assert daemon._start_daemon_subprocess(port=free_port) is False
    assert str(log) in caplog.text
    assert "database could not open" in caplog.text
    assert "old unrelated" not in caplog.text


def test_missing_log_still_reports_child_exit(spawn, tmp_path, free_port, caplog):
    spawn[0].poll.side_effect = lambda: (tmp_path / "logs" / "daemon.log").unlink(
        missing_ok=True,
    ) or 7
    assert daemon._start_daemon_subprocess(port=free_port) is False
    assert "7" in caplog.text and "daemon.log" in caplog.text


def test_startup_error_redacts_credentials_and_bounds_output(tmp_path):
    from superlocalmemory.cli.daemon_start_error import startup_error

    log = tmp_path / "daemon.log"
    value = "synthetic-test-password"
    log.write_text(f"ERROR password={value} " + "x" * 2000)
    message = startup_error(log, 0, 1)
    assert value not in message
    assert "[REDACTED:" in message
    assert len(message) < len(str(log)) + 620
