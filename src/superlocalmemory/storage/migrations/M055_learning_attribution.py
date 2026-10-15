# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M055 — durable attribution for the learning loop (learning.db only).

WHAT IT ADDS
------------
``bandit_plays.outcome_id`` plus a partial UNIQUE index over it. A reported
outcome is evidence about ONE recall. The settler used to keep the set of
outcomes it had spent in a local variable, so the next scheduler pass spent the
same outcome again on another play: one "that helped" moved three arms across
three passes. Writing the consuming outcome's id on the play, in
the same transaction as the posterior update, and letting SQLite refuse a
second play with the same id, makes the spend exactly-once across passes,
restarts and concurrent settlers.

``shadow_assignments``: which arm a recall was routed to while a candidate
ranker was under test, and which model actually served it. The shadow test
used to recompute the arm from the query id at settle time and never record
that the candidate had served anything — in fact it never did.
Persisting the assignment at serve time is what lets a settled reward be
credited to the model that produced the ranking, survive a restart, and be
deduplicated when a query id is replayed.

``learning_model_state.metadata_json``. Promotion, rollback and the online
retrain trigger all read and write this column — the retrain cooldown, the
"last retrained at" stamp, the post-rollback safe mode — and no migration ever
created it. On a real store ``_promote_candidate`` therefore failed with "no
such column" and returned False, rollback could not stamp its cooldown, and
``_should_retrain`` always answered no. The unit tests passed because they
build the table by hand with the column present.

WHY learning.db ONLY
--------------------
Both tables it touches live there. memory.db is not changed, so the upgrade
safety copy is of the smaller database only.

OLDER BUILDS
------------
Additive: a build that predates this never names the column or the table, so
``BREAKING_VERSION`` is 0 and ``DOWNGRADE_FLOOR`` stays 51 like M053/M054.
"""

from __future__ import annotations

import sqlite3

NAME = "M055_learning_attribution"
DB_TARGET = "learning"

#: Additive: a build that predates this never reads or writes what it adds.
BREAKING_VERSION = 0

#: ``prepare_downgrade`` may lower the recorded schema version to this.
DOWNGRADE_FLOOR = 51

_PLAYS = "bandit_plays"
_PLAY_COLUMN = "outcome_id"
_PLAY_INDEX = "idx_plays_outcome_once"
_ASSIGN = "shadow_assignments"
_MODELS = "learning_model_state"
_MODELS_COLUMN = "metadata_json"

#: Recorded for the runner's DDL hash. ``apply`` runs instead, because SQLite
#: has no ``ADD COLUMN IF NOT EXISTS``.
DDL = """
ALTER TABLE bandit_plays ADD COLUMN outcome_id TEXT;
ALTER TABLE learning_model_state ADD COLUMN metadata_json TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_plays_outcome_once
    ON bandit_plays (outcome_id) WHERE outcome_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS shadow_assignments (
    candidate_id    INTEGER NOT NULL,
    query_id        TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    arm             TEXT NOT NULL,
    served_model_id INTEGER,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (candidate_id, query_id)
);
"""

_ASSIGN_DDL = """
CREATE TABLE IF NOT EXISTS shadow_assignments (
    candidate_id    INTEGER NOT NULL,
    query_id        TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    arm             TEXT NOT NULL,
    served_model_id INTEGER,
    assigned_at     TEXT NOT NULL,
    PRIMARY KEY (candidate_id, query_id)
)
"""

_ASSIGN_COLUMNS = frozenset({
    "candidate_id", "query_id", "profile_id", "arm", "served_model_id",
    "assigned_at",
})


def _names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def verify(conn: sqlite3.Connection) -> bool:
    """End state: the assignment table; the model-state metadata column when
    that table exists; and — when M005 created the plays table — its outcome
    column and the once-only index."""
    try:
        names = _names(conn)
        if _ASSIGN not in names:
            return False
        if not _ASSIGN_COLUMNS <= _columns(conn, _ASSIGN):
            return False
        if (_MODELS in names
                and _MODELS_COLUMN not in _columns(conn, _MODELS)):
            return False
        if _PLAYS not in names:
            return True
        return (_PLAY_COLUMN in _columns(conn, _PLAYS)
                and _PLAY_INDEX in names)
    except sqlite3.Error:
        return False


def apply(conn: sqlite3.Connection) -> None:
    """Add what is missing. Idempotent; reads no row of an existing table."""
    conn.execute(_ASSIGN_DDL)
    names = _names(conn)
    if _MODELS in names and _MODELS_COLUMN not in _columns(conn, _MODELS):
        conn.execute(f"ALTER TABLE {_MODELS} ADD COLUMN {_MODELS_COLUMN} TEXT")
    if _PLAYS in names:
        if _PLAY_COLUMN not in _columns(conn, _PLAYS):
            conn.execute(f"ALTER TABLE {_PLAYS} ADD COLUMN {_PLAY_COLUMN} TEXT")
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_PLAY_INDEX} "
            f"ON {_PLAYS} ({_PLAY_COLUMN}) WHERE {_PLAY_COLUMN} IS NOT NULL"
        )
    if not verify(conn):  # pragma: no cover — defensive
        raise sqlite3.OperationalError("M055 did not reach its end state")


def repair(conn: sqlite3.Connection) -> None:
    """Identical to ``apply``: every step is additive and guarded."""
    apply(conn)


def blocks_serving(conn: sqlite3.Connection) -> bool:
    """Never: without it, settlement and shadow tests degrade, recall works."""
    return False
