# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A blank temporal date is no date: store it as NULL, and let the cache work.

WHAT WAS WRONG
The temporal channel keeps its date slices between recalls (M057) unless the
store holds a date that is not ISO -- a dynamic ``now`` would move with the
clock, so one such row turns the slice cache off for every recall. An empty
string matched that rule too: on one real store 7 events carried
``observation_date = ''``, the cache never served, and every recall re-read
and re-parsed 5,000 events (about 0.5 s of each recall, 13 Oct 2026).

An empty string is not dynamic: SQLite's julianday('') is NULL, exactly as for
NULL. New saves now store a blank date as NULL (``store_temporal_event``); this
does the same for the rows already there.

WHY IT IS SAFE
Only a date that is empty or whitespace becomes NULL; every ordering and filter
already read it as no date. The M057 update trigger keeps the cache counter in
step row by row, and the counter is recounted once at the end. Running it again
changes nothing.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

NAME = "M063_blank_temporal_dates_are_null"
DB_TARGET = "memory"
#: Data only; an older build reads NULL dates as it always has.
DOWNGRADE_FLOOR = 51

_DATES = ("observation_date", "referenced_date", "interval_start", "interval_end")

DDL = """
-- No schema change. apply() sets blank temporal_events dates to NULL.
"""


def _columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(temporal_events)")}
    except sqlite3.Error:
        return set()


def apply(conn: sqlite3.Connection) -> None:
    """Blank dates to NULL, then recount what the slice cache must not reuse."""
    present = [c for c in _DATES if c in _columns(conn)]
    changed = 0
    for column in present:
        changed += conn.execute(
            f"UPDATE temporal_events SET {column} = NULL "
            f"WHERE {column} IS NOT NULL AND TRIM({column}) = ''").rowcount
    try:
        from superlocalmemory.storage.migrations.M057_temporal_retrieval_version import _recount
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='temporal_retrieval_version'"
                        ).fetchone():
            _recount(conn)
    except sqlite3.Error as exc:  # the triggers already kept the counter in step
        logger.debug("M063: recount skipped (%s)", exc)
    if changed:
        logger.info("M063: %d blank temporal dates stored as NULL", changed)


REPAIR_NOT_APPLICABLE = (
    "Data only and idempotent: apply() can run again at any time, and the M057 "
    "triggers keep the slice-cache counter in step row by row; there is no "
    "structure to rebuild."
)
