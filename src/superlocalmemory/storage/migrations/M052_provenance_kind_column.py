# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""M052 — add ``provenance_kind`` to atomic_facts.

provenance_kind is a controlled governance tag on the retrieval unit (deepmaid
M3b shared-layer governance): ``world`` / ``private`` / ``curated`` / ``legacy``
(see ``storage.models.PROVENANCE_KINDS``). ``NULL`` — the column default and
therefore the state of every pre-existing row — means "not yet tagged", never
an implicit member of any kind. No CHECK constraint lives here because the
vocabulary is enforced at the model boundary (``validate_provenance_kind``);
the migration only carries the column.

Additive only — ALTER TABLE ADD COLUMN, nullable, no default, no data
rewrite. Idempotent via apply() + verify() + migration_log. Mirrors the M015
(pinned) pattern: fresh installs get the column from the base schema in
``storage.schema``, upgrades get it here. Deferred because atomic_facts is
bootstrapped at engine init. Depends on M046: that migration REBUILDS
atomic_facts with an explicit column list, so a provenance_kind column added
before it would be silently dropped by the rebuild.
"""

from __future__ import annotations

import sqlite3

NAME = "M052_provenance_kind_column"
DB_TARGET = "memory"

_REQUIRED_COLS = frozenset({"provenance_kind"})

DDL = """
BEGIN IMMEDIATE;
ALTER TABLE atomic_facts ADD COLUMN provenance_kind TEXT;
COMMIT;
"""


def verify(conn: sqlite3.Connection) -> bool:
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(atomic_facts)"
        ).fetchall()}
    except sqlite3.Error:
        return False
    return _REQUIRED_COLS <= cols


def apply(conn: sqlite3.Connection) -> None:
    """Apply M052 safely when the base schema already carries the column.

    New installations are created from the current base schema, whereas
    upgrades need the additive ALTER. SQLite has no ``ADD COLUMN IF NOT
    EXISTS``, so the static DDL would fail on fresh databases.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(atomic_facts)")}
    if "provenance_kind" not in cols:
        conn.execute(
            "ALTER TABLE atomic_facts ADD COLUMN provenance_kind TEXT"
        )


def repair(conn: sqlite3.Connection) -> None:
    """Re-run the idempotent apply as end-state repair (4.1.14 #133)."""
    apply(conn)


def run(db) -> None:
    """Idempotent entry point for tests/ops.

    Accepts whatever exposes ``execute`` the way ``apply`` uses it — a raw
    ``sqlite3.Connection`` (what the runner passes) or a
    ``storage.database.DatabaseManager``.
    """
    apply(db)
