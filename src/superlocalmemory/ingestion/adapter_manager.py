# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Adapter lifecycle manager — start, stop, enable, disable ingestion adapters.

All adapters run as separate subprocesses managed via PID files.
Config stored in ~/.superlocalmemory/adapters.json.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from superlocalmemory.infra.data_root import canonical_data_root, state_path
from superlocalmemory.ingestion.adapter_config import normalize_config, setup_error, validate_tier

logger = logging.getLogger("superlocalmemory.ingestion.manager")

_SLM_HOME = None  # test-only compatibility override
_ADAPTERS_CONFIG = None  # test-only compatibility override
_VALID_ADAPTERS = ("gmail", "calendar", "transcript")
_CHILDREN = {}

# Module paths for each adapter
_ADAPTER_MODULES = {
    "gmail": "superlocalmemory.ingestion.gmail_adapter",
    "calendar": "superlocalmemory.ingestion.calendar_adapter",
    "transcript": "superlocalmemory.ingestion.transcript_adapter",
}


def _slm_home() -> Path:
    return Path(_SLM_HOME) if _SLM_HOME is not None else canonical_data_root()


def _adapters_config_path() -> Path:
    if _ADAPTERS_CONFIG is not None:
        return Path(_ADAPTERS_CONFIG)
    return state_path("adapters.json")


def _adapter_log_dir() -> Path:
    return _slm_home() / "logs"


def _path_error(name: str, adapter_config: dict) -> str:
    """A saved path outside the import folders refuses the adapter (see import_roots)."""
    from superlocalmemory.ingestion.import_roots import saved_path_error

    return saved_path_error(name, adapter_config, _slm_home())


def _load_config() -> dict:
    config_path = _adapters_config_path()
    if config_path.exists():
        config, migrated = normalize_config(json.loads(config_path.read_text(encoding="utf-8")))
        if migrated:
            _save_config(config)
        return config
    return {name: {"enabled": False} for name in _VALID_ADAPTERS}


def _save_config(config: dict) -> None:
    for name, entry in config.items():
        if name in _VALID_ADAPTERS:
            validate_tier(name, entry.get("tier", "auto"))
    config_path = _adapters_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    from superlocalmemory.storage._durable_json import write_json_atomic
    write_json_atomic(config_path, config)


def _pid_file(name: str) -> Path:
    return _slm_home() / f"adapter-{name}.pid"


def _is_running(name: str) -> tuple[bool, int | None]:
    """Check if adapter is running. Returns (running, pid)."""
    pf = _pid_file(name)
    if not pf.exists():
        return False, None
    try:
        pid = int(pf.read_text(encoding="utf-8").strip())
        try:
            import psutil
            alive = psutil.pid_exists(pid)
            if alive:
                try:
                    state = psutil.Process(pid).status()
                    alive = state not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    alive = False
            if not alive:
                pf.unlink(missing_ok=True)
            return alive, pid
        except ImportError:
            from superlocalmemory.core.platform_utils import is_pid_alive
            return is_pid_alive(pid), pid
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        pf.unlink(missing_ok=True)
        return False, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_adapters() -> list[dict]:
    """List all adapters with their status."""
    config = _load_config()
    result = []
    for name in _VALID_ADAPTERS:
        ac = config.get(name, {})
        running, pid = _is_running(name)
        path_error = _path_error(name, ac)
        error = path_error or setup_error(name, ac)
        child = _CHILDREN.get(name)
        if child is not None and ((pid is not None and child.pid != pid) or not ac.get("enabled")):
            _CHILDREN.pop(name, None)
            child = None
        completed = False
        if child is not None and child.poll() is not None:
            running = False
            # A refused path is the reason: such a worker reads nothing and
            # exits, which must never read as "completed" or a bare exit.
            completed = child.poll() == 0 and not path_error
            error = path_error or ("" if completed else (
                "The worker exited. Check its setup in Configure, then retry Start."
            ))
        result.append({
            "name": name,
            "enabled": ac.get("enabled", False),
            "running": running,
            "pid": pid,
            "tier": ac.get("tier", ""),
            "watch_dir": ac.get("watch_dir", ""),
            "setup_error": error,
            "state": ("completed" if completed else "failed" if error and ac.get("enabled")
                      else "running" if running else "stopped"),
        })
    return result


def enable_adapter(name: str) -> dict:
    """Enable an adapter in config."""
    if name not in _VALID_ADAPTERS:
        return {"ok": False, "error": f"Unknown adapter: {name}. Valid: {_VALID_ADAPTERS}"}
    config = _load_config()
    config.setdefault(name, {})["enabled"] = True
    _save_config(config)
    return {"ok": True, "message": f"{name} adapter enabled. Run `slm adapters start {name}` to start."}


def disable_adapter(name: str) -> dict:
    """Disable an adapter. Stops it if running."""
    if name not in _VALID_ADAPTERS:
        return {"ok": False, "error": f"Unknown adapter: {name}"}
    stop_adapter(name)
    config = _load_config()
    config.setdefault(name, {})["enabled"] = False
    _save_config(config)
    return {"ok": True, "message": f"{name} adapter disabled"}


def start_adapter(name: str) -> dict:
    """Start an adapter subprocess."""
    if name not in _VALID_ADAPTERS:
        return {"ok": False, "error": f"Unknown adapter: {name}"}

    config = _load_config()
    if not config.get(name, {}).get("enabled"):
        return {"ok": False, "error": f"{name} not enabled. Run `slm adapters enable {name}` first."}
    error = _path_error(name, config.get(name, {})) or setup_error(name, config.get(name, {}))
    if error:
        return {"ok": False, "error": error}

    running, pid = _is_running(name)
    if running:
        return {"ok": True, "message": f"{name} already running (PID {pid})"}

    module = _ADAPTER_MODULES.get(name)
    if not module:
        return {"ok": False, "error": f"No module for {name}"}

    cmd = [sys.executable, "-m", module]
    log_dir = _adapter_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"adapter-{name}.log"

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    with open(log_path, "a", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=lf,
                                env={**os.environ, "SLM_DATA_DIR": str(_slm_home())}, **kwargs)
    _CHILDREN[name] = proc
    time.sleep(0.1)
    if proc.poll() is not None:
        return {"ok": False, "error": (
            "The worker exited during setup. Configure it and retry Start."
        )}

    _pid_file(name).write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "message": f"{name} started (PID {proc.pid})", "pid": proc.pid}


def stop_adapter(name: str) -> dict:
    """Stop a running adapter."""
    _CHILDREN.pop(name, None)
    running, pid = _is_running(name)
    if not running:
        return {"ok": True, "message": f"{name} not running"}

    try:
        import psutil
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=10)
    except ImportError:
        # No psutil: best-effort termination. On Windows os.kill(pid, 15) maps to
        # TerminateProcess (a hard kill, not a graceful SIGTERM) — psutil's
        # terminate() does the same there, so behavior is equivalent either way.
        os.kill(pid, 15)
    except Exception:
        pass

    _pid_file(name).unlink(missing_ok=True)
    return {"ok": True, "message": f"{name} stopped"}


def status_adapters() -> list[dict]:
    """Get detailed status of all adapters."""
    return list_adapters()


# ---------------------------------------------------------------------------
# CLI handler (called from commands.py)
# ---------------------------------------------------------------------------

def handle_adapters_cli(args: list[str]) -> None:
    """Handle `slm adapters <action> [name]` commands."""
    if not args:
        args = ["list"]

    action = args[0]
    name = args[1] if len(args) > 1 else ""

    if action == "list":
        adapters = list_adapters()
        print("  Ingestion Adapters:")
        print("  " + "-" * 50)
        for a in adapters:
            status = "running" if a["running"] else ("enabled" if a["enabled"] else "disabled")
            pid_str = f" (PID {a['pid']})" if a["pid"] else ""
            print(f"  {a['name']:12s} {status:10s}{pid_str}")
        print()

    elif action == "enable":
        if not name:
            print("  Usage: slm adapters enable <gmail|calendar|transcript>")
            return
        result = enable_adapter(name)
        print(f"  {result.get('message', result.get('error', ''))}")

    elif action == "disable":
        if not name:
            print("  Usage: slm adapters disable <name>")
            return
        result = disable_adapter(name)
        print(f"  {result.get('message', result.get('error', ''))}")

    elif action == "start":
        if not name:
            print("  Usage: slm adapters start <name>")
            return
        result = start_adapter(name)
        print(f"  {result.get('message', result.get('error', ''))}")

    elif action == "stop":
        if not name:
            print("  Usage: slm adapters stop <name>")
            return
        result = stop_adapter(name)
        print(f"  {result.get('message', result.get('error', ''))}")

    elif action == "status":
        adapters = status_adapters()
        for a in adapters:
            status = "RUNNING" if a["running"] else ("enabled" if a["enabled"] else "off")
            print(f"  {a['name']:12s} [{status}]", end="")
            if a["pid"]:
                print(f"  PID={a['pid']}", end="")
            print()

    elif action in ("allow-folder", "folders"):
        _import_folders_cli(action, name)

    else:
        print(f"  Unknown action: {action}")
        print("  Usage: slm adapters <list|enable|disable|start|stop|status> [name]")
        print("         slm adapters allow-folder <path> | folders")


def _import_folders_cli(action: str, path: str) -> None:
    """The owner's own list of folders the dashboard may point importers at."""
    from superlocalmemory.ingestion import import_roots

    home = _slm_home()
    if action == "allow-folder":
        if not path:
            print("  Usage: slm adapters allow-folder <path>")
            return
        try:
            real = import_roots.approve(path, home)
        except (OSError, ValueError) as exc:
            print(f"  Not approved: {exc}")
            return
        print(f"  Approved as an import folder: {real}")
        return
    print("  Import folders (the dashboard can choose files only inside these):")
    for root in (import_roots.default_root(home), *import_roots.approved(home)):
        print(f"    {root}")
