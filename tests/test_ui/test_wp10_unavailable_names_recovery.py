"""Every "daemon unavailable" message names the recovery command when one is needed.

PR 251 round 4, R4: while a restore waited for recovery, `slm remember`, MCP
clients and hooks said only that the daemon was unavailable and pointed at
`slm serve start`; only that command then explained. Now the diagnosis they
all share checks the recovery marker first.
"""

from __future__ import annotations

import pytest

from superlocalmemory.server import recovery_marker

COMMAND = "slm backup restore-safety-copy"


@pytest.fixture
def needs_recovery(tmp_path, monkeypatch):
    from superlocalmemory.cli import daemon

    recovery_marker.needs_recovery(tmp_path, "the restore and its rollback both failed",
                                   "0" * 16, None)
    monkeypatch.setattr(daemon, "_recovery_root", lambda: tmp_path)
    return tmp_path


def test_the_shared_diagnosis_puts_recovery_first(needs_recovery):
    from superlocalmemory.cli.daemon import describe_daemon_unavailability

    diagnosis = describe_daemon_unavailability()
    assert diagnosis["reason"] == "recovery_needed"
    assert COMMAND in diagnosis["message"] + diagnosis["hint"]


def test_slm_remember_names_the_command(needs_recovery, capsys):
    from superlocalmemory.cli.commands import _daemon_unavailable

    with pytest.raises(SystemExit):
        _daemon_unavailable("remember", False)
    assert COMMAND in capsys.readouterr().err


def test_mcp_clients_get_the_command(needs_recovery):
    from superlocalmemory.mcp._daemon_proxy import daemon_unavailable_error

    assert COMMAND in daemon_unavailable_error()


def test_the_codex_start_hook_names_the_command():
    from superlocalmemory.hooks.codex_handlers import _session_message

    message = _session_message({"error": "DAEMON_UNAVAILABLE (recovery_needed): stores mixed"})
    assert COMMAND in message


def test_the_claude_start_hook_names_the_command(needs_recovery, monkeypatch, capsys, tmp_path):
    import subprocess

    from superlocalmemory.hooks import hook_handlers

    monkeypatch.setattr(hook_handlers, "_MARKER", str(tmp_path / "m"))
    monkeypatch.setattr(hook_handlers, "_START_TIME", str(tmp_path / "s"))
    monkeypatch.setattr(hook_handlers, "_ACTIVITY_LOG", str(tmp_path / "a"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    hook_handlers._hook_start()
    assert COMMAND in capsys.readouterr().out


def test_without_a_marker_the_diagnosis_is_unchanged(tmp_path, monkeypatch):
    from superlocalmemory.cli import daemon

    monkeypatch.setattr(daemon, "_recovery_root", lambda: tmp_path)
    assert daemon.describe_daemon_unavailability()["reason"] != "recovery_needed"
