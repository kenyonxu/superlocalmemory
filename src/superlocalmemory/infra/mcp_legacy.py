"""Read-only discovery of pre-registry stdio processes in this exact namespace.

Their loaded release cannot be reconstructed from a stable executable path.
Report it as unreported. Reconnect requires the same user, data root, MCP
command and process start identity again immediately before termination.
No process environment or command line is returned to a caller.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path

import psutil

from superlocalmemory.infra.data_root import DATA_ROOT_ALIASES


def _processes():
    return psutil.process_iter(["pid", "ppid", "cmdline"])


def _get_process(pid: int):
    return psutil.Process(pid)


def _stdio_command(command: list[str]) -> bool:
    if len(command) >= 3 and command[1] == "-m":
        module = command[2]
        return (
            module == "superlocalmemory.mcp.server"
            or module in {"superlocalmemory.cli", "superlocalmemory.cli.main"}
            and len(command) >= 4
            and command[3] == "mcp"
        )
    if len(command) >= 2 and Path(command[0]).name in {"slm", "slm.exe"} and command[1] == "mcp":
        return True
    # A console script started through its shebang reports the interpreter first.
    return (
        len(command) >= 3
        and Path(command[0]).name.lower().startswith("python")
        and Path(command[1]).name in {"slm", "slm.exe", "slm-script.py"}
        and command[2] == "mcp"
    )


def _same_owner(process) -> bool:
    if hasattr(os, "getuid"):
        return process.uids().real == os.getuid()
    return process.username() == psutil.Process().username()


def _root_matches(process, environment: dict, root: Path) -> bool:
    selected = next(
        (environment.get(key) for key in DATA_ROOT_ALIASES if environment.get(key)), None
    )
    implicit = not selected
    if implicit:
        home = environment.get("HOME") or environment.get("USERPROFILE")
        if not home or not os.path.isabs(home) or ".." in Path(home).parts:
            return False
        selected = str(Path(home) / ".superlocalmemory")
    if not selected or not os.path.isabs(selected) or ".." in Path(selected).parts:
        # A relative root may have been resolved before a cwd change; it does
        # not establish this namespace.
        return False

    # Normalize without inspecting another installation's files. These are
    # macOS's system aliases; arbitrary user symlinks remain unproven.
    def normalize(path):
        value = os.path.normcase(os.path.normpath(str(path)))
        if sys.platform == "darwin" and value.startswith(("/private/tmp/", "/private/var/")):
            value = value[len("/private") :]
        return value

    if normalize(selected) != normalize(root):
        return False
    if implicit:
        # A default folder that carries a relocation hint proves nothing about
        # where the process really writes. Only the matching folder is read.
        from superlocalmemory.infra.data_root import _legacy_configured_root

        return _legacy_configured_root(Path(selected)) is None
    return True


def _host(process, environment: dict) -> str:
    from superlocalmemory.infra.mcp_processes import HOST_NAMES, infer_host

    explicit = environment.get("SLM_MCP_HOST", "")
    if explicit in HOST_NAMES:
        return explicit
    if environment.get("CLAUDECODE") == "1":
        return "claude-code"
    return infer_host([ancestor.name() for ancestor in process.parents()[:8]])


def _row(process, root: Path) -> dict | None:
    from superlocalmemory.infra.mcp_processes import HOST_NAMES, process_identity

    info = (
        process.info
        if hasattr(process, "info") and process.info
        else {
            "cmdline": process.cmdline(),
            "ppid": process.ppid(),
        }
    )
    if not _stdio_command(info.get("cmdline") or []) or not _same_owner(process):
        return None
    if info.get("ppid", 0) <= 1:
        return None
    parent = process.parent()
    if parent is None or not parent.is_running():
        return None
    started = process.create_time()
    if time.time() - started < 10:
        return None  # give a current server time to finish its handshake
    environment = process.environ()
    if not _root_matches(process, environment, root):
        return None
    identity = process_identity(process.pid)
    if identity is None:
        return None
    host = _host(process, environment)
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return {
        "session_id": f"legacy_{process.pid}_{suffix}",
        "pid": process.pid,
        "process_identity": identity,
        "host": host,
        "name": HOST_NAMES[host],
        "code_version": "unreported",
        "version_reported": False,
        "stale_version": False,
        "needs_reconnect": True,
        "tools": [],
        "connected_at": started,
        "connection_evidence": "live-process",
    }


def discover(root: Path, known_pids: set[int]) -> list[dict]:
    rows = []
    for process in _processes():
        if process.pid in known_pids:
            continue
        try:
            row = _row(process, root)
        except Exception:  # noqa: BLE001 - one unreadable process must not hide the rest
            continue  # unprovable process metadata never establishes a connection
        if row is not None:
            rows.append(row)
    return rows


def reconnect(root: Path, session: str) -> bool:
    match = re.fullmatch(r"legacy_([0-9]+)_[0-9a-f]{16}", session)
    if not match:
        return False
    try:
        process = _get_process(int(match.group(1)))
        row = _row(process, root)
        if row is None or row["session_id"] != session:
            return False
        process.terminate()  # psutil additionally guards PID reuse before signaling
        return True
    except (psutil.Error, OSError, ValueError, TypeError):
        return False
