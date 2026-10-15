# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Make a SQLite file stop holding the rows that were deleted from it.

``DELETE`` removes a row from the table, not its bytes from the file: the page
goes on the free list still holding the text, and the write-ahead log keeps the
pages it has not folded back yet. An erasure that stops at ``DELETE`` leaves the
words on disk where anyone with the file can read them. A rewrite (``VACUUM``)
plus an emptied log removes them.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_ATTEMPTS = 5


def scrub(path: str | Path, *, timeout: float = 10.0) -> bool:
    """Rewrite ``path`` without its free pages and truncate its log.

    ``True`` when both happened; ``False`` when the file is busy or damaged after
    several tries, in which case it may still hold deleted bytes and the caller
    must say so. A file that does not exist is clean.
    """
    target = Path(path)
    if not target.exists():
        return True
    for attempt in range(_ATTEMPTS):
        try:
            conn = sqlite3.connect(str(target), timeout=timeout, isolation_level=None)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
                busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            finally:
                conn.close()
            if not busy or int(busy[0]) == 0:
                return True
        except sqlite3.Error as exc:
            logger.warning("scrub of %s attempt %d failed: %s", target.name, attempt + 1, exc)
        time.sleep(0.2 * (attempt + 1))
    return False
