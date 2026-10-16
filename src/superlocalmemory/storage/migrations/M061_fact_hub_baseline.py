# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""M061: each fact's hub baseline, for recall that knows when it doesn't know.

One new table (``retrieval/hub_baseline.py``): a fact's mean cosine against a
fixed, versioned set of probe queries, in the embedding space it was measured
in. Rows are filled by a bounded, resumable background pass and at save time;
recall only reads them. Ids and numbers, never memory text. A trigger removes a
fact's row when the fact is deleted, so erasure leaves nothing behind, and
another when its vector changes, so a re-embedded fact is measured again
rather than judged on a stale baseline.
"""

from __future__ import annotations

import sqlite3

NAME = "M061_fact_hub_baseline"
DB_TARGET = "memory"
#: Additive (one new table and one trigger on delete; an older build never
#: reads the table, and the trigger touches only that table), so going back to
#: 4.1.18 stays possible.
DOWNGRADE_FLOOR = 51
DDL = """
CREATE TABLE IF NOT EXISTS fact_hub_baseline (
    fact_id       TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL,
    space         TEXT NOT NULL,
    probe_version TEXT NOT NULL,
    baseline_mean REAL NOT NULL,
    baseline_topk REAL NOT NULL,
    computed_at   REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_hub_baseline_fact_erased
AFTER DELETE ON atomic_facts
BEGIN
    DELETE FROM fact_hub_baseline WHERE fact_id = OLD.fact_id;
END;
"""
_REEMBED = """
CREATE TRIGGER IF NOT EXISTS trg_hub_baseline_fact_reembedded
AFTER UPDATE OF embedding ON atomic_facts
BEGIN
    DELETE FROM fact_hub_baseline WHERE fact_id = NEW.fact_id;
END
"""


def _has_facts(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='atomic_facts'").fetchone())


_TABLE, _TRIGGER = (part.strip() for part in DDL.split("CREATE TRIGGER", 1))


def _has_embedding(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "embedding" for r in conn.execute("PRAGMA table_info(atomic_facts)"))


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE.rstrip(";"))
    if _has_facts(conn):  # the triggers need the table they watch
        conn.execute("CREATE TRIGGER " + _TRIGGER)
        if _has_embedding(conn):
            conn.execute(_REEMBED)


def verify(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN ('fact_hub_baseline', "
        "'trg_hub_baseline_fact_erased', 'trg_hub_baseline_fact_reembedded')")}
    if "fact_hub_baseline" not in names:
        return False
    if not _has_facts(conn):
        return True
    return "trg_hub_baseline_fact_erased" in names and (
        not _has_embedding(conn) or "trg_hub_baseline_fact_reembedded" in names)


def repair(conn: sqlite3.Connection) -> None:
    apply(conn)
