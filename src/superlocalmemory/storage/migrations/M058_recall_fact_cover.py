# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""M058: fact-ID joins read visibility and time without vector overflow pages.

Unlike the old profile-led visibility index, this serves cross-scope fact-ID
joins too. Content is deliberately absent: BM25 loads it after its rank limit.
"""

from __future__ import annotations

import sqlite3

NAME = "M058_recall_fact_cover"
DB_TARGET = "memory"
#: Additive (one covering index; an older build ignores it), so going back to 4.1.18 stays possible.
DOWNGRADE_FLOOR = 51
_COLUMNS = {"fact_id", "profile_id", "scope", "shared_with", "archive_status",
            "quarantined", "created_at"}
DDL = """
CREATE INDEX IF NOT EXISTS idx_recall_fact_cover ON atomic_facts(
    fact_id,profile_id,scope,shared_with,archive_status,quarantined,created_at
);
"""


def _supported(conn: sqlite3.Connection) -> bool:
    return _COLUMNS <= {r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)")}


def apply(conn: sqlite3.Connection) -> None:
    if _supported(conn):
        conn.execute(DDL)


def verify(conn: sqlite3.Connection) -> bool:
    return not _supported(conn) or bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='idx_recall_fact_cover'").fetchone())


def repair(conn: sqlite3.Connection) -> None:
    apply(conn)
