# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""M056: indexed name lookups, temporal joins and sparse contradictions.

Recall uses NOCASE predicates so the existing alias index is reusable. Temporal joins
have no owner restriction on the event table (visibility belongs to the fact),
so its (profile_id, entity_id) index cannot serve an entity-only seek. The
contradiction query was scanning the entire profile's covering edge index.
These indexes preserve every row and the existing SQL ranking expressions.
"""

from __future__ import annotations

import sqlite3

NAME = "M056_recall_channel_indexes"
DB_TARGET = "memory"
#: Additive (indexes only; an older build ignores them), so going back to 4.1.18 stays possible.
DOWNGRADE_FLOOR = 51

DDL = """
CREATE INDEX IF NOT EXISTS idx_recall_entity_name
    ON canonical_entities(canonical_name COLLATE NOCASE, entity_id);
CREATE INDEX IF NOT EXISTS idx_recall_temporal_entity
    ON temporal_events(entity_id, fact_id);
CREATE INDEX IF NOT EXISTS idx_recall_contradictions
    ON graph_edges(profile_id, source_id, target_id, edge_type)
    WHERE edge_type = 'contradiction';
CREATE INDEX IF NOT EXISTS idx_recall_metrics_time
    ON fact_importance(profile_id, computed_at DESC);
"""

_NAMES = ("idx_recall_entity_name", "idx_recall_temporal_entity",
          "idx_recall_contradictions", "idx_recall_metrics_time")
_TABLES = ("canonical_entities", "temporal_events", "graph_edges",
           "fact_importance")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def apply(conn: sqlite3.Connection) -> None:
    """The runner owns the transaction; never commit midway through indexes."""
    tables = _tables(conn)
    for table, statement in zip(_TABLES, DDL.strip().rstrip(";").split(";"), strict=True):
        if table in tables:
            conn.execute(statement)


def verify(conn: sqlite3.Connection) -> bool:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    tables = _tables(conn)
    return all(name in names for name, table in zip(_NAMES, _TABLES, strict=True)
               if table in tables)


def repair(conn: sqlite3.Connection) -> None:
    apply(conn)
