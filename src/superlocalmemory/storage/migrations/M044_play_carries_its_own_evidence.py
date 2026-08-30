# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M044 — a bandit play records which memories it actually showed.

WHY
---
A play is settled later, from evidence: did anything downstream reference one of
the memories this query surfaced? ``reward_proxy`` answered "which memories" by
reading ``learning_signals`` for the same ``query_id`` — rows written by the
recall path's ``enqueue(SignalBatch(...))``.

That enqueue is off, deliberately. It writes twenty exposure rows per query, it
was the source of a 2,675x inflation in the ranking-phase counter, and the code
comment gives a second reason: *"even a non-blocking enqueue eventually writes
canonical/learning state and turns dashboard polling into contention."*

So the evidence lookup had no evidence to read, and every settlement fell
through to the 120-second default of 0.5. Measured on a live production store::

    SUM(alpha) = SUM(beta) = 867.5 = 165 priors + 1,405 plays x 0.5

An exact match on both sides across 165 arms: every one of 1,405 settlements
applied the neutral prior. Thompson sampling on Beta(a, a) is a coin flip, so
retrieval strategy was being chosen at random.

The fix is for the play to carry its own evidence. One column, written once per
recorded play, holding the handful of fact_ids that were actually shown — rather
than reinstating twenty rows per query in a table that gates a phase counter.

WHY A COLUMN AND NOT A TABLE
----------------------------
This is one small JSON array per play, read exactly once by the settler and
then dead. A child table would need its own index, its own retention sweep, and
its own profile_id for erasure. The column is deleted with its play by the
existing ``retention_sweep``, which is the behaviour we want and get for free.

BACK-COMPATIBILITY
------------------
Existing rows keep NULL. The settler falls back to the ``learning_signals``
lookup when the column is empty, so plays recorded before this migration settle
exactly as they did before — and installs that still run the enqueue keep
working unchanged.
"""

from __future__ import annotations

import sqlite3

NAME = "M044_play_carries_its_own_evidence"
DB_TARGET = "learning"

_TABLE = "bandit_plays"
_COLUMN = "shown_fact_ids"

#: Recorded for the runner's DDL hash. SQLite has no ``ADD COLUMN IF NOT
#: EXISTS``, so ``apply()`` below runs instead of this string; it is kept
#: accurate because the hash is what detects a migration being edited after it
#: has shipped.
DDL = """
ALTER TABLE bandit_plays ADD COLUMN shown_fact_ids TEXT;
"""


def _columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {
            row[1] for row in conn.execute(f"PRAGMA table_info({_TABLE})")
        }
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def verify(conn: sqlite3.Connection) -> bool:
    """End state: the column exists.

    Everything ``verify`` asserts must be produced by every path through
    ``apply`` — including the path where the table is absent. 4.0.10 shipped an
    M043 whose ``verify`` required a table its ``apply`` created only
    conditionally, which made the migration fail permanently on stores that
    lacked it. Hence the branch below: no table means M005 has not run, this
    migration has nothing to do, and saying so is the end state.
    """
    if not _table_exists(conn):
        return True
    return _COLUMN in _columns(conn)


def apply(conn: sqlite3.Connection) -> None:
    """Add the column if it is missing. Idempotent.

    Runs instead of ``DDL`` (the runner prefers a module-level ``apply``)
    because ``ALTER TABLE ... ADD COLUMN`` raises when the column is already
    there, and SQLite cannot guard that inside one script.
    """
    if not _table_exists(conn):
        # M005 owns bandit_plays and is a declared dependency, so this means a
        # store where the bandit tables were never created. Nothing to alter.
        return
    if _COLUMN in _columns(conn):
        return
    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} TEXT")
    if not verify(conn):  # pragma: no cover — defensive
        raise sqlite3.OperationalError(
            f"M044: {_TABLE}.{_COLUMN} absent after ALTER"
        )


def repair(conn: sqlite3.Connection) -> None:
    """Identical to ``apply`` — and that is safe only because it is additive.

    ``repair`` == ``apply`` is a trap in general: if ``verify`` can return a
    false negative, the runner retries forever. Here ``verify`` reads
    ``PRAGMA table_info``, which is the same fact ``apply`` establishes, so a
    false negative would require the ALTER to have silently not happened.
    """
    apply(conn)
