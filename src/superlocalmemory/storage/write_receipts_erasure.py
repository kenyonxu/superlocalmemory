# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""The one governed way an erased profile's write receipts are removed.

``write_commits`` is an append-only ledger: triggers abort every UPDATE and
DELETE so that no ordinary code path can rewrite history. Every accepted write
leaves a row, so every profile that ever received a memory through the daemon
has some, and the generic erasure sweep (``DELETE ... WHERE profile_id = ?``)
hit the trigger on every real profile erase: the profile's facts were deleted,
the sweep failed, and the result said so only after the receipt had been
written as COMPLETE.

The rows are not content (the ledger refuses a receipt that carries text), but
they carry the profile id, the client's idempotency keys and operation ids, and
they outlive their purpose once the profile is gone. What must survive an
erasure is the proof that it happened, and that lives in ``erasure_receipts``
and the audit chain, not here.

So the trigger is lifted for this purpose only, inside ONE transaction: DDL is
transactional in SQLite, so no other connection ever sees the table without its
triggers, and a failure rolls the triggers back with everything else. The
triggers are re-created from their own stored definitions and checked.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_TRIGGERS = ("trg_write_commits_immutable_update", "trg_write_commits_immutable_delete")


class ReceiptErasureError(RuntimeError):
    """The receipts could not be removed, or the ledger's protection could not be restored."""


def erase_profile_receipts(db_path: str | Path, profile_id: str) -> int:
    """Delete every ``write_commits`` row of ``profile_id``; returns how many.

    Raises :class:`ReceiptErasureError` when the rows remain or the triggers
    cannot be proven back in place. Nothing else in the ledger is touched.
    """
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='write_commits'").fetchone() is None:
            return 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            removed = _delete_with_triggers_lifted(conn, profile_id)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return removed
    except sqlite3.Error as exc:
        raise ReceiptErasureError(f"write receipts could not be erased: {exc}") from exc
    finally:
        conn.close()


def _delete_with_triggers_lifted(conn: sqlite3.Connection, profile_id: str) -> int:
    definitions = {
        row["name"]: row["sql"] for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='write_commits'")
        if row["name"] in _TRIGGERS
    }
    for name in definitions:
        conn.execute(f'DROP TRIGGER "{name}"')
    removed = conn.execute(
        "DELETE FROM write_commits WHERE profile_id = ?", (profile_id,)).rowcount
    for sql in definitions.values():
        conn.execute(sql)
    present = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='write_commits'")}
    if not set(definitions) <= present:
        raise ReceiptErasureError("the ledger's protection could not be restored")
    left = conn.execute(
        "SELECT COUNT(*) FROM write_commits WHERE profile_id = ?", (profile_id,)).fetchone()[0]
    if left:
        raise ReceiptErasureError(f"{left} receipts remain")
    return int(removed)
