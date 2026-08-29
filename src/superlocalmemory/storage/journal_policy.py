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
"""

from __future__ import annotations

import os
import sqlite3

DEFAULT_JOURNAL_MODE = "delete"


def resolve_journal_mode() -> str:
    """Return the journal mode every new connection should request."""
    mode = (os.environ.get("SLM_JOURNAL_MODE") or "").strip().lower()
    if mode:
        return mode
    return DEFAULT_JOURNAL_MODE


def apply_journal_mode(conn: sqlite3.Connection) -> str:
    """Apply the configured journal mode; return the resulting mode."""
    conn.execute(f"PRAGMA journal_mode={resolve_journal_mode()}")
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]) if row else ""
