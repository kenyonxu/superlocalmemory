"""Installed and active are independent states of a Linux user service."""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from superlocalmemory.cli import service_installer, service_inventory


def _row(name: str = "superlocalmemory") -> SimpleNamespace:
    return SimpleNamespace(name=name, port=8765)


@pytest.mark.parametrize(
    "unit_exists,stdout,installed,active",
    [
        (False, "unknown", False, False),
        (False, "inactive", False, False),
        (True, "inactive", True, False),
        (True, "active", True, True),
        (True, "failed", True, False),
    ],
)
def test_linux_status(unit_exists, stdout, installed, active, monkeypatch):
    rows = [_row()] if unit_exists else []
    monkeypatch.setattr(service_inventory, "definitions", lambda platform: rows)
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout + "\n", ""))
    monkeypatch.setattr(service_inventory, "_run", run)
    status = service_installer.status_linux()
    assert status["installed"] is installed
    assert status["active"] is active
    # Without a unit definition there is nothing to ask systemd about.
    assert run.call_count == (1 if unit_exists else 0)


@pytest.mark.parametrize("error", [FileNotFoundError, subprocess.TimeoutExpired])
def test_linux_status_without_systemctl_keeps_file_evidence(error, monkeypatch):
    monkeypatch.setattr(service_inventory, "definitions", lambda platform: [_row()])
    failure = error("systemctl", 10) if error is subprocess.TimeoutExpired else error()
    monkeypatch.setattr(service_inventory, "_run", MagicMock(side_effect=failure))
    status = service_installer.status_linux()
    assert status["installed"] is True
    assert status["active"] is False
