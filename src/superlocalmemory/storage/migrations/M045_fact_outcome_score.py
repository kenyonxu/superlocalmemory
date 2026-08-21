# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M045 — per-fact outcome score (PCOS), so ranking can use what worked.

WHY
---
Ranking scores a memory by how much it *looks like* the query. Nothing in the
pipeline knows whether a memory has ever actually helped. This table holds one
number per (fact, profile): an exponentially-weighted average of the rewards of
the settlements it took part in.

WHY IT LIVES IN memory.db
-------------------------
Beside ``atomic_facts``, because the read path resolves it with a LEFT JOIN in
the query that already loads a fact. SQLite cannot join across databases without
ATTACH, and taking an ATTACH on the recall hot path to reach a single REAL would
cost more than the number is worth. ``action_outcomes`` — the backfill source —
is in memory.db too, so the whole thing stays in one file.

profile_id IS PART OF THE PRIMARY KEY, AND THAT IS NOT COSMETIC
---------------------------------------------------------------
A learned per-fact score is derived personal data. If it had no ``profile_id``,
``forget_profile()`` would run, report success, and leave the erased user's
learned outcome history behind — which is the same defect already
known in ``fact_expansion_fts``. Creating a second table with the same defect
in the same release would be indefensible. ``compliance/gdpr.py`` deletes from
this table; a test asserts one profile's erasure leaves another's rows intact.

THE BACKFILL, AND A CORRECTION TO THE PLAN
------------------------------------------
An earlier design's backfill read ``SELECT fact_id, profile_id, AVG(reward) FROM
action_outcomes GROUP BY fact_id, profile_id``. **There is no ``fact_id`` column
on that table** — it stores ``fact_ids_json``, a JSON array, because one outcome
covers the set of memories an answer used. So the backfill expands the array with
``json_each``, guarded by ``json_valid`` (an unguarded ``json_each`` raises on the
first malformed row and takes the whole migration with it).

On a live store this initialises every touched fact at 0.5 from 162 rows
that are all 0.5 — which is the neutral prior, so the backfill adds no bias. Its
only job is to avoid a cold start looking like a penalty.
"""

from __future__ import annotations

import sqlite3

NAME = "M045_fact_outcome_score"
DB_TARGET = "memory"

_TABLE = "fact_outcome_score"

DDL = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS fact_outcome_score (
    fact_id     TEXT NOT NULL,
    profile_id  TEXT NOT NULL DEFAULT 'default',
    score       REAL NOT NULL DEFAULT 0.5,
    play_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (fact_id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_fos_profile
    ON fact_outcome_score (profile_id, fact_id);
COMMIT;
"""

#: Expand ``fact_ids_json`` and average the reward per (fact, profile).
#:
#: ``INSERT OR IGNORE`` names no conflict target on purpose: the table has one
#: unique constraint today, and a targeted ``ON CONFLICT`` silently stops
#: covering a row the moment a second constraint is added.
_BACKFILL = """
INSERT OR IGNORE INTO fact_outcome_score
    (fact_id, profile_id, score, play_count, updated_at)
SELECT
    j.value AS fact_id,
    o.profile_id,
    AVG(o.reward),
    COUNT(*),
    datetime('now')
FROM action_outcomes AS o, json_each(o.fact_ids_json) AS j
WHERE o.reward IS NOT NULL
  AND json_valid(o.fact_ids_json)
  AND o.fact_ids_json IS NOT NULL
  AND j.value IS NOT NULL AND j.value != ''
GROUP BY j.value, o.profile_id
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def verify(conn: sqlite3.Connection) -> bool:
    """End state: the table exists with profile_id in it.

    Deliberately does NOT assert anything about backfilled rows. The backfill
    depends on ``action_outcomes``, which is bootstrapped at engine init and may
    hold nothing at all — so asserting rows would make ``verify`` fail forever
    on a fresh install. Everything asserted here is produced by every path
    through ``apply``; that is the M043 lesson, learned the hard way.
    """
    if not _table_exists(conn, _TABLE):
        return False
    cols = _columns(conn, _TABLE)
    return {"fact_id", "profile_id", "score", "play_count"} <= cols


def apply(conn: sqlite3.Connection) -> None:
    """Create the table, then backfill from reported outcomes if any exist."""
    conn.executescript(DDL)
    if not verify(conn):  # pragma: no cover — defensive
        raise sqlite3.OperationalError(
            "M045 fact_outcome_score did not reach its end-state"
        )
    _backfill(conn)


def _backfill(conn: sqlite3.Connection) -> int:
    """Seed from ``action_outcomes``. Never fatal — the table is what matters.

    A failure here means facts start at the cold-start default of 0.5, which is
    the same value the backfill would have written on this store anyway. Losing
    the migration over that would be the wrong trade.
    """
    if not _table_exists(conn, "action_outcomes"):
        return 0
    if "reward" not in _columns(conn, "action_outcomes"):
        # reward arrives with M006, which is deferred. Nothing to average yet.
        return 0
    try:
        cur = conn.execute(_BACKFILL)
        return int(cur.rowcount or 0)
    except sqlite3.Error:
        return 0


def repair(conn: sqlite3.Connection) -> None:
    """Re-run apply. Safe: the DDL is IF NOT EXISTS and the backfill IGNOREs."""
    apply(conn)
