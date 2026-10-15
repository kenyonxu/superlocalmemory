# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The trigger that forgets a hub baseline when its memory is re-embedded.

M061 creates ``trg_hub_baseline_fact_reembedded`` as ``AFTER UPDATE OF
embedding ON atomic_facts``: a vector that changed makes its baseline stale, so
the row goes and the backfill measures it again.

An embedding switch swaps the vector columns by ``ALTER TABLE ... RENAME
COLUMN`` (``embedding_canonical_slots.swap``), and SQLite rewrites a trigger
that names a renamed column. The trigger therefore followed the OLD vectors to
``embedding_next``. The runner then cleared those with ``SET embedding_next =
NULL``, which fired the trigger once per memory and deleted every baseline:
measured on a real 27k-memory store, 27,236 rows before the switch and 0 after.
The vector change-log trigger had the same exposure and is re-created in
``swap``; this one was missed.

``rebind`` re-creates it on the live column. ``swap`` calls it, and the
background backfill calls ``is_bound`` first, so a store a 4.2.1 switch already
damaged is repaired.
"""

from __future__ import annotations

import re
from typing import Any

NAME = "trg_hub_baseline_fact_reembedded"
TABLE = "fact_hub_baseline"
LIVE_COLUMN = "embedding"

DDL = (
    f"CREATE TRIGGER {NAME} AFTER UPDATE OF {LIVE_COLUMN} ON atomic_facts "
    f"BEGIN DELETE FROM {TABLE} WHERE fact_id = NEW.fact_id; END"
)

_BOUND = re.compile(r'UPDATE\s+OF\s+["`\[]?embedding["`\]]?\s+ON\s+', re.IGNORECASE)


def is_bound(sql: str | None) -> bool:
    """True when ``sql`` (from sqlite_master) watches the live vector column."""
    return bool(sql) and _BOUND.search(sql) is not None


def _has(conn: Any, kind: str, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
                        (kind, name)).fetchone() is not None


def rebind(conn: Any) -> bool:
    """Re-create the trigger on the live column; True when it changed anything.

    Nothing to do (False) when the baseline table or the vector column is not
    there. The caller owns the transaction.
    """
    if not _has(conn, "table", TABLE):
        return False
    if not any(r[1] == LIVE_COLUMN for r in conn.execute("PRAGMA table_info(atomic_facts)")):
        return False
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                       (NAME,)).fetchone()
    if row is not None and is_bound(row[0]):
        return False
    conn.execute(f"DROP TRIGGER IF EXISTS {NAME}")
    conn.execute(DDL)
    return True


__all__ = ["DDL", "LIVE_COLUMN", "NAME", "TABLE", "is_bound", "rebind"]
