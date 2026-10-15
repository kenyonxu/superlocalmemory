# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""OS-level service installer — daemon survives reboots.

Cross-platform:
  - macOS:   LaunchAgent plist (user-level, no sudo)
  - Linux:   systemd user service (no sudo)
  - Windows: Task Scheduler (runs at logon)

Usage:
  slm serve install   — install OS service
  slm serve uninstall — remove OS service
  slm serve status    — show daemon + service status
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from superlocalmemory.cli import service_inventory
from superlocalmemory.cli.deployment import (
    configured_port,
    security_environment,
    service_identity,
    systemd_security_lines,
    windows_security_lines,
)
from superlocalmemory.infra.data_root import canonical_data_root, state_path
from superlocalmemory.infra.service_mutation import serialized

logger = logging.getLogger(__name__)

_DISPLAY_NAME = "SuperLocalMemory Daemon"


def get_python_path() -> str:
    """Get the full path to the Python interpreter running SLM."""
    return sys.executable


def get_log_path() -> Path:
    log_dir = state_path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "daemon.log"


def get_error_log_path() -> Path:
    log_dir = state_path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "daemon-error.log"


# ─── macOS: LaunchAgent ───────────────────────────────────────────────────

def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{service_identity()}.plist"


def _macos_plist_content() -> str:
    import plistlib

    python = get_python_path()
    log = get_log_path()
    err_log = get_error_log_path()
    port = str(_configured_daemon_port())
    payload = {
        "Label": service_identity(),
        "ProgramArguments": [
            python,
            "-m",
            "superlocalmemory.server.unified_daemon",
            "--start",
            f"--port={port}",
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(err_log),
        "EnvironmentVariables": {
            "PATH": f"/usr/local/bin:/usr/bin:/bin:{Path(python).parent}",
            "HOME": str(Path.home()),
            "SLM_DATA_DIR": str(canonical_data_root()),
            "SLM_DAEMON_PORT": port,
            **security_environment(),
        },
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False).decode()


@serialized
def install_macos() -> bool:
    plist = _macos_plist_path()
    if not service_inventory.prepare_install("macos", plist):
        return False
    if not service_inventory.write(plist, _macos_plist_content()):
        return False
    logger.info("Wrote LaunchAgent plist: %s", plist)

    result = subprocess.run(
        ["launchctl", "load", str(plist)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        logger.info("LaunchAgent loaded successfully")
        return True
    else:
        logger.error("launchctl load failed: %s", result.stderr)
        return False


def uninstall_macos() -> bool:
    return service_inventory.uninstall("macos")


def status_macos() -> dict:
    return {
        "platform": "macOS", "service_type": "LaunchAgent",
        "plist_path": str(_macos_plist_path()),
        **service_inventory.status("macos"),
    }


# ─── Linux: systemd user service ──────────────────────────────────────────

def _linux_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{service_identity()}.service"


def _linux_service_content() -> str:
    python = get_python_path()
    log = get_log_path()
    root = canonical_data_root()
    port = _configured_daemon_port()

    return f"""[Unit]
Description={_DISPLAY_NAME}
After=network.target

[Service]
Type=simple
ExecStart={python} -m superlocalmemory.server.unified_daemon --start --port={port}
Restart=on-failure
RestartSec=30
StandardOutput=append:{log}
StandardError=append:{get_error_log_path()}
Environment=HOME={Path.home()}
Environment=PATH=/usr/local/bin:/usr/bin:/bin:{Path(python).parent}
Environment="SLM_DATA_DIR={root}"
Environment="SLM_DAEMON_PORT={port}"
{systemd_security_lines()}

[Install]
WantedBy=default.target
"""


@serialized
def install_linux() -> bool:
    service = _linux_service_path()
    if not service_inventory.prepare_install("linux", service):
        return False
    if not service_inventory.write(service, _linux_service_content()):
        return False
    logger.info("Wrote systemd user service: %s", service)

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
        subprocess.run(
            ["systemctl", "--user", "enable", service_identity()], capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["systemctl", "--user", "start", service_identity()],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("systemd user service started")
            return True
        else:
            logger.error("systemctl start failed: %s", result.stderr)
            return False
    except FileNotFoundError:
        logger.warning("systemctl not found — systemd not available on this system")
        return False


def uninstall_linux() -> bool:
    return service_inventory.uninstall("linux")


def status_linux() -> dict:
    return {
        "platform": "Linux", "service_type": "systemd user service",
        **service_inventory.status("linux"),
    }


# ─── Windows: Task Scheduler ─────────────────────────────────────────────

def _configured_daemon_port() -> int:
    return configured_port()


def _windows_vbs_content() -> str:
    python = str(get_python_path()).replace('"', '""')
    root = str(canonical_data_root()).replace('"', '""')
    port = str(_configured_daemon_port())
    return (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        + windows_security_lines() +
        f'WshShell.Environment("Process")("SLM_DATA_DIR") = "{root}"\n'
        f'WshShell.Environment("Process")("SLM_DAEMON_PORT") = "{port}"\n'
        f'WshShell.Run """{python}"" -m superlocalmemory.server.unified_daemon '
        f'--start --port={port}", 0, False\n'
    )


@serialized
def install_windows() -> bool:
    # Create a VBS wrapper to run Python without console window
    vbs_path = state_path(f"start-daemon-{service_identity()}.vbs")
    if not service_inventory.prepare_install("windows", vbs_path):
        return False
    if not service_inventory.write(vbs_path, _windows_vbs_content()):
        return False

    # Existing owned tasks were removed by prepare_install.
    try:
        result = subprocess.run(
            [
                "schtasks", "/Create",
                "/TN", service_identity(),
                "/TR", f'wscript.exe "{vbs_path}"',
                "/SC", "ONLOGON",
                "/RL", "LIMITED",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("Windows Task Scheduler task created: %s", service_identity())
            return True
        else:
            logger.error("schtasks create failed: %s", result.stderr)
            return False
    except FileNotFoundError:
        logger.warning("schtasks not found — not a standard Windows system")
        return False


def uninstall_windows() -> bool:
    return service_inventory.uninstall("windows")


def status_windows() -> dict:
    return {
        "platform": "Windows", "service_type": "Task Scheduler",
        "task_name": service_identity(),
        **service_inventory.status("windows"),
    }


# ─── Cross-platform dispatcher ───────────────────────────────────────────

def install_service() -> bool:
    """Install OS-level service for auto-start on login/boot."""
    if sys.platform == "darwin":
        return install_macos()
    elif sys.platform == "win32":
        return install_windows()
    elif sys.platform.startswith("linux"):
        return install_linux()
    else:
        logger.warning("Unsupported platform: %s", sys.platform)
        return False


def uninstall_service() -> bool:
    """Remove OS-level service."""
    if sys.platform == "darwin":
        return uninstall_macos()
    elif sys.platform == "win32":
        return uninstall_windows()
    elif sys.platform.startswith("linux"):
        return uninstall_linux()
    else:
        return False


def service_status() -> dict:
    """Get OS-level service status."""
    if sys.platform == "darwin":
        return status_macos()
    elif sys.platform == "win32":
        return status_windows()
    elif sys.platform.startswith("linux"):
        return status_linux()
    else:
        return {"platform": sys.platform, "installed": False, "details": "Unsupported platform"}
