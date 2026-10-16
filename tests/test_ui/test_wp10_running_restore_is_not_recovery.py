"""A restore that is running says "wait"; only a broken one says "recover".

PR 251 round 5, S2: while a restore was genuinely running, MCP clients and
the Codex start hook told the user to run the recovery command, and the CLI
marked recovery_needed as retryable although retrying never clears it.
"""

from __future__ import annotations

import json

import pytest

from superlocalmemory.server import recovery_marker

COMMAND = "slm backup restore-safety-copy"


@pytest.fixture
def root(tmp_path, monkeypatch):
    from superlocalmemory.cli import daemon

    monkeypatch.setattr(daemon, "_recovery_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def running(root):
    recovery_marker.begin(root, "0" * 16)  # this process is the live writer
    return root


@pytest.fixture
def broken(root):
    recovery_marker.needs_recovery(root, "rollback failed", "0" * 16, None)
    return root


def test_a_running_restore_has_its_own_reason_and_no_recovery_hint(running):
    from superlocalmemory.cli.daemon import describe_daemon_unavailability

    diagnosis = describe_daemon_unavailability()
    assert diagnosis["reason"] == "restore_running"
    assert COMMAND not in diagnosis["hint"]
    assert "wait" in diagnosis["hint"].lower()


def test_mcp_clients_are_told_to_wait(running):
    from superlocalmemory.mcp._daemon_proxy import daemon_unavailable_error

    text = daemon_unavailable_error()
    assert "restore_running" in text and "wait" in text.lower()


def test_the_codex_hook_says_wait_for_a_running_restore():
    from superlocalmemory.hooks.codex_handlers import _session_message

    message = _session_message({"error": "DAEMON_UNAVAILABLE (restore_running): busy"})
    assert "wait" in message.lower() and COMMAND not in message


def _cli_error(capsys):
    from superlocalmemory.cli.commands import _daemon_unavailable

    with pytest.raises(SystemExit):
        _daemon_unavailable("remember", True)
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):])["error"]


def test_recovery_needed_is_not_retryable(broken, capsys):
    error = _cli_error(capsys)
    assert error["reason"] == "recovery_needed" and error["retryable"] is False


def test_a_running_restore_is_retryable(running, capsys):
    error = _cli_error(capsys)
    assert error["reason"] == "restore_running" and error["retryable"] is True
