# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""One row per schema version, which is what every writer already assumed.

WHAT WAS WRONG

``schema_version`` records which schema versions have been applied. Six call
sites write to it and every one of them uses ``INSERT OR IGNORE``, which reads
as "record this version unless it is already recorded".

There was no unique constraint on ``version``, and no index at all. With nothing
to conflict against, ``OR IGNORE`` never ignores anything, so each of those call
sites appended a duplicate on every run. Measured:

    store          rows      distinct versions
    live          3,496                      7
    larger      234,348                      7

That is the same seven facts written a third of a million times, and it grows
every time the daemon starts or a maintenance cycle re-checks the schema.

WHY THE INDEX AND NOT A CLEANUP JOB

A retention rule would delete the duplicates and leave the cause, so the table
would refill. The unique index makes ``INSERT OR IGNORE`` do what all six call
sites already believe it does, which fixes the cause and makes the cleanup a
one-off.

WHAT IS KEPT

The earliest row for each version -- the one recording when that version was
actually first applied, which is the only one of the duplicates that carries
true information. Where a duplicate set disagrees on ``description`` the first
non-empty one wins, because later writers pass ``''``.

WHY THE TABLE IS REBUILT AND NOT ALTERED

SQLite cannot add a constraint to an existing table, and a UNIQUE INDEX cannot
be created over data that already violates it -- so the duplicates come out
first, in the same transaction that adds the index. If the two were separate,
a writer between them would insert a duplicate and the index creation would
fail on a store that had just been cleaned.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

NAME = "M049_a_schema_version_marker_is_one_row"
DB_TARGET = "memory"

#: Additive: an index and fewer duplicate rows. An older build reading this
#: table asks whether a version is present, which is unchanged. Writing to it
#: with OR IGNORE now succeeds silently instead of appending, which is what the
#: older build intended anyway.
BREAKING_VERSION = 0

_TABLE = "schema_version"
_INDEX = "idx_schema_version_unique"

DDL = """
-- Deduplicate schema_version, keeping the earliest row per version, then make
-- the column unique so INSERT OR IGNORE stops appending.
"""


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
    ).fetchone()
    return row is not None


def apply(conn: sqlite3.Connection) -> None:
    """Collapse the duplicates and add the constraint, in one transaction."""
    if not _has_table(conn):
        logger.info("M049: no %s table; nothing to do", _TABLE)
        return

    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({_TABLE})")}
    if "version" not in columns:
        logger.info("M049: %s has no version column; nothing to do", _TABLE)
        return
    has_description = "description" in columns
    has_applied_at = "applied_at" in columns

    before = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    distinct = conn.execute(
        f"SELECT COUNT(DISTINCT version) FROM {_TABLE}"
    ).fetchone()[0]

    order_by = "applied_at ASC, rowid ASC" if has_applied_at else "rowid ASC"
    # The row to keep for each version, chosen by when the version was recorded
    # as landing. ``MIN(rowid)`` will not do: an ORDER BY inside a grouped
    # subquery does not decide which row MIN() picks, so on a store where the
    # duplicates were written out of order it kept a later stamp and deleted the
    # original. Reproduced: rows dated 2026-01-01 and 2026-08-01 for one
    # version, and the January one -- the true first application -- was the one
    # that went.
    survivor = (
        f"SELECT outer_row.rowid FROM {_TABLE} AS outer_row "
        f"WHERE outer_row.rowid = ("
        f"  SELECT inner_row.rowid FROM {_TABLE} AS inner_row"
        f"  WHERE inner_row.version = outer_row.version"
        f"  ORDER BY {order_by.replace('applied_at', 'inner_row.applied_at').replace('rowid', 'inner_row.rowid')}"
        f"  LIMIT 1)"
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        if has_description:
            # A later duplicate may carry the only real description, and after
            # the delete there is nothing left to read it from. So this runs
            # FIRST, moving the earliest non-empty description onto the row that
            # is about to survive. Running it afterwards -- as this once did --
            # could only turn NULL into an empty string, which recovers nothing
            # and reads in the log as though it had.
            conn.execute(
                f"UPDATE {_TABLE} SET description = COALESCE(("
                f"  SELECT other.description FROM {_TABLE} AS other"
                f"  WHERE other.version = {_TABLE}.version"
                f"    AND other.description IS NOT NULL"
                f"    AND TRIM(other.description) <> ''"
                f"  ORDER BY {order_by}"
                f"  LIMIT 1), '') "
                f"WHERE description IS NULL OR TRIM(description) = ''"
            )
        conn.execute(
            f"DELETE FROM {_TABLE} WHERE rowid NOT IN ({survivor})"
        )
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE}(version)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    after = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]
    logger.info(
        "M049: %s went from %d row(s) to %d for %d distinct version(s)",
        _TABLE, before, after, distinct,
    )


def repair(conn: sqlite3.Connection) -> None:
    """Re-run. Idempotent: the delete and the index are both conditional."""
    apply(conn)


def verify(conn: sqlite3.Connection) -> bool:
    """One row per version, and a constraint that keeps it that way.

    Both halves matter. Row count alone would pass on a freshly deduplicated
    store that is about to refill; the index alone would pass on a store where
    the index exists but was created before the duplicates were removed, which
    SQLite would not allow but a hand-edited store could reach.
    """
    if not _has_table(conn):
        return True
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({_TABLE})")}
    if "version" not in columns:
        return True

    total, distinct = conn.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT version) FROM {_TABLE}"
    ).fetchone()
    if total != distinct:
        logger.error(
            "M049 verify: %s holds %d rows for %d versions", _TABLE, total, distinct
        )
        return False

    for row in conn.execute(f"PRAGMA index_list({_TABLE})"):
        name, unique = row[1], row[2]
        if not unique:
            continue
        indexed = [c[2] for c in conn.execute(f"PRAGMA index_info({name})")]
        if indexed == ["version"]:
            return True
    logger.error("M049 verify: %s has no unique index on version", _TABLE)
    return False
