# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com

"""Process-level per-path write-lock registry for memory.db.

ALL code that writes to a given SQLite file MUST acquire the lock returned
by ``get_write_lock(db_path)`` BEFORE opening a sqlite3 connection for
writing.  This serialises writes at the Python layer so that only ONE
sqlite3 connection holds the SQLite WAL write lock at any instant, which
eliminates all SQLITE_BUSY retries within the process.

Invariant
---------
Every memory.db write path in the process acquires the same RLock before
touching sqlite3.  Because SQLite WAL mode allows one writer at a time, a
Python-level RLock gives us the same serialisation guarantee while
completely avoiding the SQLITE_BUSY / busy_timeout retry loop.

Lock ordering rule (must never be violated)
-------------------------------------------
    get_write_lock(memory.db)  ←  OUTERMOST
        ↳  VectorStore._lock   (inner — VectorStore in-memory state)
            ↳  sqlite3 write transaction (BEGIN … COMMIT)

Re-entrancy
-----------
``threading.RLock`` is used so that the same thread can re-acquire the
lock without deadlocking.  This is required by the self-heal backfill
pattern::

    with db._lock:          # db._lock IS get_write_lock(memory.db)
        vs.upsert(...)      # also calls get_write_lock → re-entrant OK

Usage
-----
    from superlocalmemory.storage.write_lock import get_write_lock

    with get_write_lock(db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO ...")
        conn.commit()
        conn.close()
"""
from __future__ import annotations

import threading
from pathlib import Path

# Registry: resolved absolute path string → RLock.
# Protected by its own Lock so the registry itself is thread-safe.
_registry: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def get_write_lock(db_path: str | Path) -> threading.RLock:
    """Return the process-level write-serialisation RLock for *db_path*.

    Resolves the path to its canonical absolute form before hashing so
    that relative paths, ``.`` components, and symlinks all map to the
    same lock as their resolved target.  Creates a new ``RLock`` on first
    call for each unique resolved path.  Thread-safe.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  The file need not exist yet.

    Returns
    -------
    threading.RLock
        The shared write-serialisation lock for *db_path*.  All writers
        of this file in the current process share the same object.
    """
    p = Path(db_path)
    try:
        key = str(p.resolve())
    except OSError:
        # File doesn't exist yet; use absolute path as best-effort key.
        key = str(p.absolute())

    with _registry_lock:
        if key not in _registry:
            _registry[key] = threading.RLock()
        return _registry[key]


__all__ = ["get_write_lock"]
