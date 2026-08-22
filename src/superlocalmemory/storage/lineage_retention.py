# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Forget the provenance of things that no longer exist.

`derivation_lineage` records where each derived object came from — which source
span produced this fact, this graph edge, this scene. Every ingestion operation
re-captures lineage for the objects present at that moment, so the table grows
with use, and nothing has ever deleted from it.

Measured on a real 447 MB store:

    derivation_lineage           256,885 rows, 64 MB, plus 65 MB of indexes
    ... describing a graph edge
        that no longer exists    100,581 rows — 39.2% of the table
    growth                       ~9,000 rows/day, sustained

The graph is pruned. Its lineage was not, so the record of how a deleted edge
came to exist outlives the edge forever. Those rows answer no question: the
evidence bundle computes lineage coverage over the objects it exports, and an
object that is gone is not exported.

WHAT IS AND IS NOT DELETED

Only a row whose object is provably absent — the type is one this module knows
how to resolve, the table exists, and no row with that id is in it. An
unrecognised object type is left alone, because "I do not know what this
describes" is not evidence that it describes nothing.

There is deliberately no age rule. Lineage is what an audit reads to answer
"where did this come from", and a fact can be years old and still current.
Deleting provenance for something that still exists would be destroying the
answer while keeping the question.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = ["OBJECT_SOURCES", "LineagePruneReport", "count_orphan_lineage",
           "prune_orphan_lineage"]

#: object_type -> (table holding it, column carrying its id).
#: Mirrors what ``core/derivation_lineage.capture_operation_lineage`` writes.
#: A type absent from this map is never deleted.
OBJECT_SOURCES: dict[str, tuple[str, str]] = {
    "fact": ("atomic_facts", "fact_id"),
    "graph_edge": ("graph_edges", "edge_id"),
    "memory_scene": ("memory_scenes", "scene_id"),
    "entity_summary": ("entity_profiles", "profile_entry_id"),
    "index_bm25": ("bm25_tokens", "fact_id"),
    "profile": ("profiles", "profile_id"),
}

#: Rows per transaction. Big enough that commit overhead vanishes, small enough
#: that an interrupted run has done most of its work and holds no long lock.
_BATCH = 2_000


class _Rows:
    """Read and write through either a raw connection or the DatabaseManager.

    The maintenance cycle holds a ``DatabaseManager``, whose lock serialises
    every write; a migration or a test holds a plain ``sqlite3.Connection``.
    Both are legitimate callers, and the difference is two method names.
    """

    def __init__(self, db: object) -> None:
        self._db = db
        self._managed = hasattr(db, "transaction") and not isinstance(
            db, sqlite3.Connection
        )

    def query(self, sql: str, params: tuple = ()) -> list:
        rows = self._db.execute(sql, tuple(params))
        return list(rows) if self._managed else rows.fetchall()

    def write(self, sql: str, params: tuple = ()) -> None:
        if self._managed:
            with self._db.transaction():
                self._db.execute(sql, tuple(params))
            return
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(sql, tuple(params))
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise


@dataclass(frozen=True)
class LineagePruneReport:
    """What was removed, by object type, and what was left alone."""

    deleted: dict[str, int] = field(default_factory=dict)
    skipped_types: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


def _table_exists(rows: _Rows, table: str) -> bool:
    return bool(rows.query(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ))


def _resolvable(rows: _Rows) -> tuple[dict[str, tuple[str, str]], tuple[str, ...]]:
    """Split the types present in the table into resolvable and not."""
    present = {
        str(r[0]) for r in rows.query(
            "SELECT DISTINCT object_type FROM derivation_lineage"
        )
    }
    resolvable: dict[str, tuple[str, str]] = {}
    skipped: list[str] = []
    for object_type in sorted(present):
        source = OBJECT_SOURCES.get(object_type)
        if source is None or not _table_exists(rows, source[0]):
            skipped.append(object_type)
            continue
        resolvable[object_type] = source
    return resolvable, tuple(skipped)


def count_orphan_lineage(
    db: object, *, profile_id: str | None = None,
) -> LineagePruneReport:
    """How many rows describe something absent, without deleting anything."""
    rows = _Rows(db)
    if not _table_exists(rows, "derivation_lineage"):
        return LineagePruneReport()

    resolvable, skipped = _resolvable(rows)
    counts: dict[str, int] = {}
    for object_type, (table, column) in resolvable.items():
        sql = (
            "SELECT COUNT(*) FROM derivation_lineage d WHERE d.object_type = ? "
            f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{column} = d.object_id)"
        )
        params: list[object] = [object_type]
        if profile_id is not None:
            sql += " AND d.profile_id = ?"
            params.append(profile_id)
        count = int(rows.query(sql, tuple(params))[0][0])
        if count:
            counts[object_type] = count
    return LineagePruneReport(counts, skipped)


def prune_orphan_lineage(
    db: object,
    *,
    profile_id: str | None = None,
    dry_run: bool = False,
) -> LineagePruneReport:
    """Delete lineage rows whose object is provably gone.

    Batched and committed as it goes, so an interrupted run keeps the work it
    already did and the next one continues. Nothing here reads a clock: what is
    deleted depends only on what exists.
    """
    rows = _Rows(db)
    if not _table_exists(rows, "derivation_lineage"):
        return LineagePruneReport()

    report = count_orphan_lineage(db, profile_id=profile_id)
    if dry_run or not report.total:
        return report

    resolvable, skipped = _resolvable(rows)
    deleted: dict[str, int] = {}

    for object_type, (table, column) in resolvable.items():
        if object_type not in report.deleted:
            continue
        removed = 0
        while True:
            sql = (
                "SELECT lineage_id FROM derivation_lineage d "
                "WHERE d.object_type = ? "
                f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{column} = d.object_id)"
            )
            params: list[object] = [object_type]
            if profile_id is not None:
                sql += " AND d.profile_id = ?"
                params.append(profile_id)
            sql += f" LIMIT {_BATCH}"

            ids = [r[0] for r in rows.query(sql, tuple(params))]
            if not ids:
                break
            placeholders = ",".join("?" * len(ids))
            rows.write(
                f"DELETE FROM derivation_lineage WHERE lineage_id IN ({placeholders})",
                tuple(ids),
            )
            removed += len(ids)
        if removed:
            deleted[object_type] = removed
            logger.info(
                "lineage retention: removed %d row(s) describing a %s that no "
                "longer exists", removed, object_type,
            )

    if skipped:
        logger.info(
            "lineage retention: left %s alone — no table is known to hold them",
            ", ".join(skipped),
        )
    return LineagePruneReport(deleted, skipped)
