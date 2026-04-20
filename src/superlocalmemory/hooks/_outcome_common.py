# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — Track A.2 (LLD-09)

"""Shared helpers for the three outcome-population hooks (LLD-09).

All helpers are stdlib-only, never raise, and bound their work by budget.
Used by:
  - ``post_tool_outcome_hook`` (hot path, <10 ms typical, <20 ms hard)
  - ``user_prompt_rehash_hook`` (hot path, <10 ms typical, <20 ms hard)
  - ``stop_outcome_hook``       (session-end, <500 ms typical, <1 s hard)

Contract refs:
  - LLD-00 §1.2 — pending_outcomes lives in memory.db, NOT cache.db.
  - LLD-00 §3   — HMAC marker validator for fact_id matching.
  - LLD-00 §4   — safe_resolve_identifier for any path built from session_id.
  - MASTER-PLAN §2 I1 — hot-path p95 budget.

This module is the single source of truth for:
  1. Locating memory.db (respecting SLM_HOME override used in tests).
  2. Opening a short-lived sqlite3 connection with busy_timeout=50.
  3. Reading/writing session_state/<session_id>.json with path-escape
     defence.
  4. Appending one NDJSON line to logs/hook-perf.log.
"""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import IO, Optional


# ---------------------------------------------------------------------------
# Budget constants
# ---------------------------------------------------------------------------

#: Hot-path SQLite busy timeout (ms). Fail fast rather than block a host tool.
BUSY_TIMEOUT_MS: int = 50

#: Cap on tool_response bytes scanned — bounds substring work to O(100 KB).
SCAN_BYTES_CAP: int = 100_000

#: Re-query detection window (ms). Outside → no signal.
REQUERY_WINDOW_MS: int = 60_000

# SEC-M4 — perf log rotation. Cap at 10 MB; keep one rotated copy
# (``hook-perf.log.1``). Bounds disk growth + limits info-disclosure
# window on multi-year retention.
PERF_LOG_MAX_BYTES: int = 10 * 1024 * 1024
PERF_LOG_CHECK_EVERY: int = 256  # check size every N writes, not every write


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def slm_home() -> Path:
    """Return ``~/.superlocalmemory`` honouring ``SLM_HOME`` override.

    ``SLM_HOME`` exists solely so unit tests can isolate filesystem state.
    Production code sets nothing and falls back to the home-directory path.

    SEC-M6 — first-creation chmod's the dir to 0700 so the audit marker
    in ``ram_lock.sem`` (``{pid}:{name}``) and session-state files are
    not world-readable on shared hosts.
    """
    override = os.environ.get("SLM_HOME", "").strip()
    base = Path(override) if override else (Path.home() / ".superlocalmemory")
    try:
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(base, 0o700)  # SEC-M6
    except Exception:  # pragma: no cover — read-only fs / perms
        pass
    return base


def memory_db_path() -> Path:
    """Canonical memory.db path (hosts pending_outcomes + action_outcomes)."""
    return slm_home() / "memory.db"


def session_state_dir() -> Path:
    """Per-session JSON state directory (created on demand).

    SEC-M3 — chmod 0700 so session_state/*.json (topic_sig, outcome_id)
    side-channels are not readable by other UIDs.
    """
    d = slm_home() / "session_state"
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(d, 0o700)  # SEC-M3
    except Exception:  # pragma: no cover — disk full / ro fs
        pass
    return d


def perf_log_path() -> Path:
    d = slm_home() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(d, 0o700)  # SEC-M4 — logs dir private
    except Exception:  # pragma: no cover
        pass
    return d / "hook-perf.log"


# ---------------------------------------------------------------------------
# SQLite — short-lived connection with busy_timeout
# ---------------------------------------------------------------------------


def open_memory_db() -> sqlite3.Connection:
    """Open memory.db with the hot-path busy timeout + autocommit.

    Caller is responsible for ``close()``. We intentionally do NOT enable
    WAL here — the daemon already set it on first boot; hooks are writers
    to a WAL DB and must not flip the journal mode under a live daemon.
    """
    conn = sqlite3.connect(
        str(memory_db_path()),
        timeout=2.0,
        isolation_level=None,  # autocommit — each statement is its own txn
    )
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Session state — path-escape-hardened read/write
# ---------------------------------------------------------------------------


def session_state_file(session_id: str) -> Path | None:
    """Resolve ``<session_state_dir>/<session_id>.json`` via the LLD-00
    §4 identifier validator. Returns ``None`` if ``session_id`` is unsafe.
    """
    try:
        from superlocalmemory.core.security_primitives import (
            safe_resolve_identifier,
        )
    except Exception:  # pragma: no cover — SLM import broken
        return None
    base = session_state_dir()
    try:
        path = safe_resolve_identifier(base, session_id)
    except ValueError:
        return None
    return path.with_suffix(".json") if path.suffix != ".json" else path


def load_session_state(session_id: str) -> dict:
    """Read session state JSON; ``{}`` on any failure."""
    p = session_state_file(session_id)
    if p is None or not p.exists():
        return {}
    try:
        raw = p.read_text()
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return {}
    return {}


def save_session_state(session_id: str, state: dict) -> None:
    """Persist session state JSON (best-effort; never raises).

    # H-12/M-P-06: atomic temp-file + os.replace so a hook killed
    # mid-write cannot leave a truncated JSON on disk. A truncated file
    # would make ``load_session_state`` return ``{}`` and silently
    # forfeit the rehash signal on the next turn.
    """
    p = session_state_file(session_id)
    if p is None:
        return
    try:
        data = json.dumps(state)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(data)
        os.replace(tmp, p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool-response size guard
# ---------------------------------------------------------------------------


def summarize_response(raw: object, cap: int = SCAN_BYTES_CAP) -> str:
    """Coerce ``raw`` to a string capped at ``cap`` bytes (UTF-8 safe).

    Claude Code passes tool_response as a string OR a structured blob; we
    str()-ify as a defensive fallback. The cap is applied before any
    regex / substring scan so the hot-path cost is O(cap) regardless of
    input size (failure mode #4 in LLD-09 §7).
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        try:
            raw = json.dumps(raw, default=str)
        except Exception:
            try:
                raw = str(raw)
            except Exception:
                return ""
    if len(raw) <= cap:
        return raw
    return raw[:cap]


# ---------------------------------------------------------------------------
# Perf log (NDJSON append, best-effort)
# ---------------------------------------------------------------------------


# M-P-01: module-level append-only fd + atexit flush/close. Previously
# ``log_perf`` opened and closed the perf log on every invocation. At
# 20 tool-events/min × 8 h that was ~9.6k gratuitous APFS metadata
# round-trips per day. The shared fd is guarded by a lock because long-
# lived daemons may call ``log_perf`` from multiple threads; POSIX
# ``write()`` is atomic for payloads ≤ PIPE_BUF but our lock keeps us
# safe across platforms and captures a post-rotation reopen cleanly.
_PERF_LOG_FD: Optional[IO[str]] = None
_PERF_LOG_PATH: Optional[Path] = None
_PERF_LOG_LOCK = threading.Lock()
_PERF_LOG_WRITE_COUNT: int = 0  # SEC-M4 — rotation cadence counter


def _open_perf_log_fd(path: Path) -> Optional[IO[str]]:
    """Open the append-only perf-log fd at mode 0600 on POSIX.

    SEC-M4 — log is private (info-disclosure surface). ``os.open`` is
    used to set the mode on creation; we wrap the fd with fdopen so the
    rest of the module sees a normal text file object.
    """
    try:
        if os.name == "posix":
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            fd_int = os.open(str(path), flags, 0o600)
            # Harden existing files that may predate this change.
            try:
                os.chmod(path, 0o600)
            except OSError:  # pragma: no cover — perms
                pass
            return os.fdopen(fd_int, "a", encoding="utf-8", buffering=1)
        return open(path, "a", encoding="utf-8", buffering=1)
    except Exception:  # pragma: no cover — disk full / perms
        return None


def _maybe_rotate_perf_log(path: Path) -> None:
    """Rotate ``hook-perf.log`` → ``hook-perf.log.1`` when over 10 MB.

    SEC-M4 — called from ``log_perf`` under ``_PERF_LOG_LOCK`` every
    ``PERF_LOG_CHECK_EVERY`` writes so the stat() cost is negligible
    on the hot path. Single rotation slot (overwrite .1 if present).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < PERF_LOG_MAX_BYTES:
        return
    rotated = path.with_suffix(path.suffix + ".1")
    try:
        if rotated.exists():
            rotated.unlink()
        path.rename(rotated)
    except OSError:  # pragma: no cover — fs race
        pass


def _perf_log_flush() -> None:
    """Flush the cached perf log fd (atexit hook). Never raises."""
    global _PERF_LOG_FD
    with _PERF_LOG_LOCK:
        fd = _PERF_LOG_FD
        _PERF_LOG_FD = None
    if fd is None:
        return
    try:
        fd.flush()
    except Exception:  # pragma: no cover
        pass
    try:
        fd.close()
    except Exception:  # pragma: no cover
        pass


atexit.register(_perf_log_flush)


def log_perf(hook_name: str, duration_ms: float, outcome: str) -> None:
    """Append one NDJSON line to ``logs/hook-perf.log``.

    Best-effort: disk full / unwritable dir → silently skip. Uses a
    module-level append-only fd opened on first use and flushed on
    process exit via :func:`_perf_log_flush`.
    """
    global _PERF_LOG_FD, _PERF_LOG_PATH, _PERF_LOG_WRITE_COUNT
    try:
        rec = {
            "ts": int(time.time() * 1000),
            "hook": hook_name,
            "duration_ms": round(duration_ms, 3),
            "outcome": outcome,
        }
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        path = perf_log_path()
        with _PERF_LOG_LOCK:
            # SEC-M4 — amortised rotation check.
            _PERF_LOG_WRITE_COUNT += 1
            if _PERF_LOG_WRITE_COUNT % PERF_LOG_CHECK_EVERY == 0:
                # Close fd before rename so POSIX release is clean.
                if _PERF_LOG_FD is not None:
                    try:
                        _PERF_LOG_FD.close()
                    except Exception:  # pragma: no cover
                        pass
                    _PERF_LOG_FD = None
                _maybe_rotate_perf_log(path)
            # Reopen if first use OR if the target path has changed (tests
            # flip ``SLM_HOME`` between cases — honour the new location).
            if _PERF_LOG_FD is None or _PERF_LOG_PATH != path:
                if _PERF_LOG_FD is not None:
                    try:
                        _PERF_LOG_FD.close()
                    except Exception:  # pragma: no cover
                        pass
                _PERF_LOG_FD = _open_perf_log_fd(path)
                _PERF_LOG_PATH = path
            fd = _PERF_LOG_FD
            if fd is None:
                return
            fd.write(line)
    except Exception:  # pragma: no cover — disk full / perms
        pass


# ---------------------------------------------------------------------------
# Entry-point helpers — shared exit-0 crash guard
# ---------------------------------------------------------------------------


def emit_empty_json() -> None:
    """Write ``{}`` to stdout. Hooks are passive observers (LLD-09 §3.4)."""
    try:
        sys.stdout.write("{}")
    except Exception:  # pragma: no cover — stdout closed
        pass


#: Upper bound on stdin bytes read per hook invocation. Claude Code
#: pipes the full tool_response through stdin; a large blob (e.g. a
#: multi-MB git log) would otherwise block the hook while the pipe
#: drains. ``summarize_response`` caps the SCANNED payload at 100 KB
#: downstream, so reading 200 KB here keeps header/envelope fields
#: intact without exceeding the hot-path budget.
STDIN_READ_CAP_BYTES: int = 200_000


def read_stdin_json() -> dict | None:
    """Read a JSON dict from stdin. Returns None on any failure.

    # H-12/M-P-05: bounded read — previously ``sys.stdin.read()`` was
    # unbounded and a multi-MB tool_response could block the hook for
    # hundreds of ms just to drain the pipe.
    """
    try:
        raw = sys.stdin.read(STDIN_READ_CAP_BYTES)
    except Exception:
        return None
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def now_ms() -> int:
    return int(time.time() * 1000)
