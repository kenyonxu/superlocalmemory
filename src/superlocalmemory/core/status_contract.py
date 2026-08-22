# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""What "status" means, in one place, for every surface that answers it.

Three surfaces answered the same question — MCP ``get_status``, the HTTP
dashboard and ``slm status`` — and returned three different field sets. Two of
them omitted the graph counts, which are the numbers that say whether the graph
is healthy; one omitted the version, which is the first thing anyone asks for
in a bug report. Each carried its own copy of the same three COUNT queries.

This module holds the agreed field set and the queries behind it. A surface may
add fields of its own — the dashboard needs a display name for the mode, the
daemon needs its own pid and uptime — but it may not be missing one of these.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Fields every status surface emits. A surface with extra fields is fine; a
#: surface missing one of these is a defect, and a test asserts it.
CANONICAL_STATUS_FIELDS: tuple[str, ...] = (
    "mode",
    "provider",
    "profile",
    "base_dir",
    "db_path",
    "db_size_mb",
    "fact_count",
    "entity_count",
    "edge_count",
    "profile_generation",
    "version",
)

#: The counts, and the one query each comes from. Profile-scoped without
#: exception — a count that ignores the active profile is a wrong answer on any
#: machine with more than one workspace.
#:
#: ``fact_count`` is completed at call time with the visibility predicate: it is
#: the number an owner reads as "how much do I remember", so soft-deleted and
#: withheld rows must not be in it. Counting them raw is how the dashboard came
#: to report 5,317 against 4,018 real memories on the author's store.
COUNT_QUERIES: dict[str, str] = {
    "fact_count": "SELECT COUNT(*) FROM atomic_facts WHERE profile_id = ?",
    "entity_count": "SELECT COUNT(*) FROM canonical_entities WHERE profile_id = ?",
    "edge_count": "SELECT COUNT(*) FROM graph_edges WHERE profile_id = ?",
}


def counts_from_sqlite(conn: sqlite3.Connection, profile_id: str) -> dict[str, int]:
    """Every count on the contract, from one connection.

    A table that is not there yet — a store mid-migration, or one built before
    the graph existed — reports zero rather than failing the whole status call.
    A status endpoint that raises tells an operator nothing.
    """
    from superlocalmemory.storage.database import visible_fact_clause_for_connection

    counts: dict[str, int] = {}
    for field, sql in COUNT_QUERIES.items():
        if field == "fact_count":
            sql += visible_fact_clause_for_connection(conn)
        try:
            row = conn.execute(sql, (profile_id,)).fetchone()
            counts[field] = int(row[0]) if row else 0
        except sqlite3.Error:
            counts[field] = 0
    return counts


def store_size_mb(db_path: Path | str | None) -> float:
    """Size of the store on disk, or 0.0 when there is nothing to measure."""
    if not db_path:
        return 0.0
    path = Path(db_path)
    try:
        return round(path.stat().st_size / (1024 * 1024), 2)
    except OSError:
        return 0.0
