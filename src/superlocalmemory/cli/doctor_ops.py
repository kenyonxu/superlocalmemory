# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""The ``slm doctor`` row for saves that failed: the same truth as ``slm ops status``.

``slm doctor`` printed 25 passes and no failures on a store whose
``slm ops status`` said DEGRADED with 60 dead-letter saves: memories whose
enrichment the system gave up on after repeated attempts, so they carry no
entities or graph links. Doctor is where a person looks when something seems
wrong, so it has to say so. The counts come from the same function the daemon's
``/status`` uses (``core.ops_remediation.get_failure_counts``), read from the
database directly, so the two cannot disagree and doctor needs no daemon.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Dead-letter saves at or above this many are a FAIL; fewer are a WARN. One
#: poisoned save is worth knowing about; fifty is a pipeline that is not working.
FAIL_AT = 50

_REASONS_SHOWN = 3
_REASON_WIDTH = 60

LIST_COMMAND = "slm ops list"
RETRY_COMMAND = "slm ops resolve <id> --action retry"
CANCEL_COMMAND = "slm ops resolve <id> --action cancel"

FIX = (
    f"Run `{LIST_COMMAND}` to see each failed operation and why it failed; then "
    f"`{RETRY_COMMAND}` runs it again, or `{CANCEL_COMMAND}` dismisses it "
    "(`slm ops status` shows the totals; the dashboard's Operations > Ops Health "
    "does the same)"
)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _dead_letter_reasons(db_path: Path) -> list[tuple[str, int]]:
    """The most common reasons dead-lettered saves gave, as ``(reason, count)``."""
    try:
        from superlocalmemory.core.ops_inspection import NOT_STALE
        from superlocalmemory.storage.memory_write import memory_read

        query = (
            "SELECT COALESCE(NULLIF(TRIM(error), ''), 'no reason recorded') AS reason, "
            "COUNT(*) AS n FROM dead_letter_operations{where} GROUP BY reason "
            "ORDER BY n DESC, reason LIMIT ?")
        with memory_read(db_path) as conn:
            try:  # saves that later completed are not failures
                rows = conn.execute(query.format(where=f" WHERE {NOT_STALE}"),
                                    (_REASONS_SHOWN,)).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(query.format(where=""), (_REASONS_SHOWN,)).fetchall()
        return [(str(r[0])[:_REASON_WIDTH], int(r[1])) for r in rows]
    except Exception:  # noqa: BLE001 -- the reasons are a courtesy; the count is the finding
        return []


def failed_saves_finding(db_path: str | Path) -> tuple[str, str, str]:
    """``(status, detail, fix)`` for the doctor row about failed operations.

    PASS when nothing failed; WARN when something did; FAIL when
    :data:`FAIL_AT` or more saves were dead-lettered.
    """
    from superlocalmemory.core.ops_remediation import get_failure_counts

    db_path = Path(db_path)
    counts = get_failure_counts(db_path)
    if counts.get("unavailable"):
        # OPS-10: unknown is not "no failures".
        return ("WARN", "the list of failed saves could not be read ("
                + ", ".join(counts["unavailable"]) + "), so failures are unknown",
                "check that memory.db is readable, then run 'slm doctor' again")
    dead = counts["dead_letter_count"]
    degraded = counts["degraded_operations"]
    exhausted = counts["exhausted_obligations"]
    if not (dead or degraded or exhausted):
        return "PASS", "no failed saves, degraded operations or exhausted retries", ""

    parts = []
    if dead:
        reasons = _dead_letter_reasons(db_path)
        why = "; ".join(f"{n} {reason}" for reason, n in reasons)
        parts.append(
            f"{_plural(dead, 'save', 'saves')} failed after every retry and were "
            "dead-lettered, so those memories may lack entities, graph links or "
            "search indexing" + (f" (most common: {why})" if why else ""))
    if degraded:
        parts.append(f"{_plural(degraded, 'save', 'saves')} finished degraded")
    if exhausted:
        parts.append(
            f"{_plural(exhausted, 'graph/vector update', 'graph/vector updates')} "
            "gave up retrying")
    status = "FAIL" if dead >= FAIL_AT else "WARN"
    return status, "; ".join(parts), FIX


__all__ = [
    "CANCEL_COMMAND", "FAIL_AT", "FIX", "LIST_COMMAND", "RETRY_COMMAND",
    "failed_saves_finding",
]
