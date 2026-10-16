"""Central journal-mode policy for all superlocalmemory SQLite files.

History: every store hard-coded ``PRAGMA journal_mode=WAL``. WAL's close
path (``sqlite3WalClose`` -> checkpoint -> ``unixLock``) can block for the
duration of a reader pin held by ANOTHER process — while holding SQLite's
process-global VFS mutex. Every later ``sqlite3.connect()`` in the host
process then convoys on ``findReusableFd`` and the embedding host silently
freezes (postmortem 2026-08-13; recurrence 2026-08-29 in the hermes
gateway, wedged mid-turn with its delivery ledger). DELETE-mode close needs
no checkpoint, so that convoy cannot form on the close path. slm's write
concurrency is low, so DELETE's reader-blocking is a non-issue here.

Resolution order:
  1. ``SLM_JOURNAL_MODE`` env var (``wal`` / ``delete`` / ...)
  2. default ``delete``

One documented exemption (merge 4.2.6, ruling S8): the learning signal
worker's own drain connection pins WAL through this module — see
:data:`SIGNAL_WORKER_JOURNAL_MODE`. The exemption lives here so the
"modes are decided in one place" invariant (tests/core/test_journal_mode.py)
keeps holding.
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_MODE = "delete"

#: The ONE connection exempt from the default: the learning signal worker's
#: own drain connection. It is a high-frequency background committer whose
#: bounded stop-path flush must persist every pending batch (SW3); under
#: DELETE each commit pays a journal-file create/delete and the shutdown
#: flush measurably dropped whole batches (8/100). The convoy this policy
#: exists to kill — WAL's close path blocked by a foreign reader while the
#: EMBEDDING host convoys on the global VFS mutex — cannot form on that
#: path: the connection is opened and closed by its own drain thread inside
#: the daemon.
SIGNAL_WORKER_JOURNAL_MODE = "wal"


def resolve_journal_mode() -> str:
    """Return the journal mode every new connection should request."""
    mode = (os.environ.get("SLM_JOURNAL_MODE") or "").strip().lower()
    if mode:
        return mode
    return DEFAULT_JOURNAL_MODE


def _set_mode(conn: sqlite3.Connection, mode: str) -> str:
    """Set ``mode`` best-effort; a locked file keeps its current mode.

    Merge 4.2.6 (ruling S8 coherence): when the flip cannot run because
    another live connection owns a different mode (the signal worker's
    exempt WAL connection, or any concurrent bootstrap), the connection
    keeps the file's current mode instead of failing — a journal mode is a
    property of the file, never worth crashing a bootstrap over. The
    requested mode still wins whenever the file is free to take it, so
    every policy decision is unchanged.
    """
    try:
        conn.execute(f"PRAGMA journal_mode={mode}")
    except sqlite3.OperationalError as exc:
        if "lock" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        logger.debug(
            "journal_mode=%s could not be set (%s); keeping the file's mode",
            mode, exc,
        )
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]) if row else ""


def apply_journal_mode(conn: sqlite3.Connection) -> str:
    """Apply the configured journal mode; return the resulting mode."""
    return _set_mode(conn, resolve_journal_mode())


def apply_signal_worker_journal_mode(conn: sqlite3.Connection) -> str:
    """The signal worker's documented WAL exemption.

    See :data:`SIGNAL_WORKER_JOURNAL_MODE`. Best-effort by the same rule as
    :func:`apply_journal_mode`: a file another connection holds in a
    different mode is left as it is, and the drain connection follows the
    file rather than failing the worker's start.
    """
    return _set_mode(conn, SIGNAL_WORKER_JOURNAL_MODE)
