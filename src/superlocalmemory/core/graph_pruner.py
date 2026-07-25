# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3.4.11 "Scale-Ready" — Graph Pruning Engine.

Reduces graph_edges count without losing meaningful connections:
  1. Orphan removal: edges where source/target no longer exists
  2. Self-loop removal: edges where source == target
  3. Duplicate removal: keeps highest-weight edge per (source, target, type)
  4. Supersedes chain collapse: A→B→C becomes A→B + A→C (B→C removed)

CRITICAL: Never deletes facts. Only prunes graph EDGES.
All operations are profile-scoped and idempotent.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger("superlocalmemory.graph_pruner")

_CHAIN_BATCH_LIMIT = 10_000
# v3.4.59: Nodes with more than this many total edges (in+out) are hub nodes.
# SA and entity_graph channels cap fan-out at 30/node, so edges beyond this
# threshold provide zero additional recall signal while bloating the graph.
_MAX_DEGREE_PER_NODE: int = 100
_HUB_PRUNE_BATCH: int = 500  # delete edges in batches to avoid giant IN clauses

# Fix A: maximum rows to DELETE per transaction (keeps lock-hold < 10 ms).
_BATCH_SIZE: int = 1_000
# Brief sleep between batch-delete transactions so other writers get a turn.
_BATCH_YIELD_S: float = 0.005


def prune_graph(
    db_or_path: "Union[DatabaseManager, str, Path]",
    profile_id: str = "default",
    dry_run: bool = False,
    cap_degree: bool = True,
) -> dict:
    """Run all graph pruning strategies for a specific profile.

    Fix A: accepts a DatabaseManager as the first argument (preferred) to
    route all writes through the process-level serialisation lock, eliminating
    the lock-storm contribution from the legacy open-own-connection pattern.

    For backward compatibility a ``str | Path`` is also accepted: a temporary
    DatabaseManager is constructed from it with a deprecation warning.

    Each DELETE strategy now runs in its own short transaction (≤ _BATCH_SIZE
    rows) with a brief yield between batches.  This caps the WAL write-lock
    hold to < 10 ms per batch instead of potentially 30+ seconds for the
    full prune.

    NOTE (Flaw 2 from CRIT): orphan IDs are read once upfront, then deleted
    in batches.  New orphan edges created by the materialiser DURING a prune
    run are NOT in the initial ID list and will be pruned on the NEXT cycle.
    The ``total_after`` stat therefore reflects reality after the last batch
    completes, which may be slightly different from total_before - removed.
    This is acceptable — no data loss, just a stat nuance.

    Returns stats dict with counts for each strategy.
    """
    from superlocalmemory.storage.database import DatabaseManager

    if isinstance(db_or_path, DatabaseManager):
        db = db_or_path
    else:
        logger.warning(
            "prune_graph: passing a db_path is deprecated — pass a "
            "DatabaseManager instead (Fix A backward-compat shim active)"
        )
        db = DatabaseManager(db_or_path)

    stats: dict = {
        "orphans_removed": 0,
        "supersedes_collapsed": 0,
        "self_loops_removed": 0,
        "duplicates_removed": 0,
        "hub_edges_removed": 0,
        "association_orphans_removed": 0,
        "total_before": 0,
        "total_after": 0,
    }

    try:
        before = db.execute(
            "SELECT COUNT(*) AS cnt FROM graph_edges WHERE profile_id = ?",
            (profile_id,),
        )
        stats["total_before"] = int(before[0]["cnt"]) if before else 0

        start = time.time()

        if not dry_run:
            stats["orphans_removed"] = _remove_orphan_edges_batched(
                db, profile_id,
            )
            stats["self_loops_removed"] = _remove_self_loops_batched(
                db, profile_id,
            )
            stats["duplicates_removed"] = _remove_duplicate_edges_batched(
                db, profile_id,
            )
            stats["supersedes_collapsed"] = _collapse_supersedes_chains_batched(
                db, profile_id,
            )
            if cap_degree:
                stats["hub_edges_removed"] = _cap_node_degree_batched(
                    db, profile_id, _MAX_DEGREE_PER_NODE,
                )
            stats["association_orphans_removed"] = (
                _remove_orphan_association_edges_batched(db, profile_id)
            )
        else:
            # Dry-run: read-only estimates via a temporary connection
            # (DatabaseManager is not used here — no writes)
            stats.update(
                _dry_run_counts(db.db_path, profile_id, cap_degree)
            )

        after = db.execute(
            "SELECT COUNT(*) AS cnt FROM graph_edges WHERE profile_id = ?",
            (profile_id,),
        )
        stats["total_after"] = int(after[0]["cnt"]) if after else 0

        elapsed = time.time() - start
        total_removed = stats["total_before"] - stats["total_after"]
        pct = round(total_removed / max(stats["total_before"], 1) * 100, 1)

        prefix = "(dry-run) " if dry_run else ""
        logger.info(
            "%sGraph pruning: removed %d edges (%.1f%%) in %.1fs — "
            "orphans=%d, supersedes=%d, self_loops=%d, duplicates=%d, hub_cap=%d",
            prefix, total_removed, pct, elapsed,
            stats["orphans_removed"], stats["supersedes_collapsed"],
            stats["self_loops_removed"], stats["duplicates_removed"],
            stats["hub_edges_removed"],
        )

    except Exception as exc:
        logger.error("Graph pruning failed: %s", exc, exc_info=True)
        stats["error"] = str(exc)

    return stats


# ---------------------------------------------------------------------------
# Batched delete helpers (Fix A — short transactions, bounded lock hold)
# ---------------------------------------------------------------------------

def _batch_delete_by_ids(
    db: "DatabaseManager",
    table: str,
    ids: list,
    id_col: str = "edge_id",
) -> int:
    """Delete rows in ``table`` where ``id_col`` IN ``ids``, _BATCH_SIZE at a time.

    Each batch runs in its own transaction so the write lock is released
    between batches. Returns total rows deleted.
    """
    removed = 0
    for start in range(0, len(ids), _BATCH_SIZE):
        batch = ids[start:start + _BATCH_SIZE]
        ph = ",".join("?" * len(batch))
        with db.transaction():
            db.execute(
                f"DELETE FROM {table} WHERE {id_col} IN ({ph})",
                tuple(batch),
            )
        removed += len(batch)
        if start + _BATCH_SIZE < len(ids):
            time.sleep(_BATCH_YIELD_S)
    return removed


def _remove_orphan_edges_batched(
    db: "DatabaseManager",
    profile_id: str,
) -> int:
    """Remove graph_edges whose source OR target no longer exists in facts/entities.

    Read all orphan IDs upfront, then batch-delete.  New orphans created
    during the run will be handled in the next maintenance cycle.
    """
    rows = db.execute(
        """
        SELECT edge_id FROM graph_edges
        WHERE profile_id = ?
          AND (
            (source_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND source_id NOT IN (SELECT entity_id FROM canonical_entities))
            OR
            (target_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND target_id NOT IN (SELECT entity_id FROM canonical_entities))
          )
        """,
        (profile_id,),
    )
    ids = [dict(r)["edge_id"] for r in rows]
    if not ids:
        return 0
    return _batch_delete_by_ids(db, "graph_edges", ids)


def _remove_self_loops_batched(
    db: "DatabaseManager",
    profile_id: str,
) -> int:
    """Remove self-loop edges (source_id == target_id)."""
    rows = db.execute(
        "SELECT edge_id FROM graph_edges "
        "WHERE source_id = target_id AND profile_id = ?",
        (profile_id,),
    )
    ids = [dict(r)["edge_id"] for r in rows]
    if not ids:
        return 0
    return _batch_delete_by_ids(db, "graph_edges", ids)


def _remove_duplicate_edges_batched(
    db: "DatabaseManager",
    profile_id: str,
) -> int:
    """Remove duplicate edges (same source+target+type), keeping highest weight."""
    rows = db.execute(
        """
        SELECT edge_id FROM graph_edges ge_outer
        WHERE profile_id = ?
          AND edge_id NOT IN (
            SELECT edge_id FROM graph_edges ge1
            WHERE profile_id = ?
              AND weight = (
                SELECT MAX(weight) FROM graph_edges ge2
                WHERE ge2.source_id = ge1.source_id
                  AND ge2.target_id = ge1.target_id
                  AND ge2.edge_type = ge1.edge_type
                  AND ge2.profile_id = ge1.profile_id
              )
            GROUP BY source_id, target_id, edge_type
          )
        """,
        (profile_id, profile_id),
    )
    ids = [dict(r)["edge_id"] for r in rows]
    if not ids:
        return 0
    return _batch_delete_by_ids(db, "graph_edges", ids)


def _collapse_supersedes_chains_batched(
    db: "DatabaseManager",
    profile_id: str,
) -> int:
    """Collapse supersedes chains: A→B, B→C becomes A→C (B→C removed)."""
    rows = db.execute(
        """
        SELECT e1.edge_id AS e1_id, e1.source_id AS a, e1.target_id AS b,
               e1.weight AS e1_weight,
               e2.edge_id AS e2_id, e2.target_id AS c
        FROM graph_edges e1
        JOIN graph_edges e2 ON e1.target_id = e2.source_id
        WHERE e1.edge_type = 'supersedes'
          AND e2.edge_type = 'supersedes'
          AND e1.profile_id = ?
          AND e2.profile_id = ?
        LIMIT ?
        """,
        (profile_id, profile_id, _CHAIN_BATCH_LIMIT),
    )
    chains = [dict(r) for r in rows]
    if not chains:
        return 0

    if len(chains) >= _CHAIN_BATCH_LIMIT:
        logger.warning(
            "Supersedes chain collapse hit limit (%d). "
            "More chains may exist — will process in next cycle.",
            _CHAIN_BATCH_LIMIT,
        )

    now = datetime.now(UTC).isoformat()
    delete_ids: list[str] = []
    insert_rows: list[tuple] = []

    for chain in chains:
        delete_ids.append(chain["e2_id"])
        new_edge_id = uuid.uuid4().hex[:16]
        insert_rows.append((
            new_edge_id, profile_id,
            chain["a"], chain["c"],
            "supersedes", chain["e1_weight"] or 1.0, now,
        ))

    # Delete B→C edges in batches, then insert A→C shortcuts
    removed = _batch_delete_by_ids(db, "graph_edges", delete_ids)

    for start in range(0, len(insert_rows), _BATCH_SIZE):
        batch = insert_rows[start:start + _BATCH_SIZE]
        with db.transaction():
            for row in batch:
                db.execute(
                    "INSERT OR IGNORE INTO graph_edges "
                    "(edge_id, profile_id, source_id, target_id, "
                    " edge_type, weight, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    row,
                )
        if start + _BATCH_SIZE < len(insert_rows):
            time.sleep(_BATCH_YIELD_S)

    return removed


def _cap_node_degree_batched(
    db: "DatabaseManager",
    profile_id: str,
    max_degree: int,
) -> int:
    """Remove low-weight edges from hub nodes (degree > max_degree).

    Uses the same window-function approach as the legacy cursor version but
    selects IDs into Python then batch-deletes through DatabaseManager.
    """
    rows = db.execute(
        """
        SELECT edge_id FROM (
            SELECT edge_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY source_id ORDER BY weight DESC
                   ) AS out_rn,
                   ROW_NUMBER() OVER (
                       PARTITION BY target_id ORDER BY weight DESC
                   ) AS in_rn
            FROM graph_edges
            WHERE profile_id = ?
        ) WHERE out_rn > ? OR in_rn > ?
        """,
        (profile_id, max_degree, max_degree),
    )
    ids = [dict(r)["edge_id"] for r in rows]
    if not ids:
        return 0
    removed = _batch_delete_by_ids(db, "graph_edges", ids)
    logger.info(
        "_cap_node_degree_batched: removed %d low-weight edges (max_degree=%d)",
        removed, max_degree,
    )
    return removed


def _remove_orphan_association_edges_batched(
    db: "DatabaseManager",
    profile_id: str,
) -> int:
    """Remove association_edges whose source/target fact no longer exists."""
    rows = db.execute(
        """
        SELECT id FROM association_edges
        WHERE profile_id = ?
          AND (
            source_fact_id NOT IN (
                SELECT fact_id FROM atomic_facts WHERE profile_id = ?
            )
            OR target_fact_id NOT IN (
                SELECT fact_id FROM atomic_facts WHERE profile_id = ?
            )
          )
        """,
        (profile_id, profile_id, profile_id),
    )
    ids = [dict(r)["id"] for r in rows]
    if not ids:
        return 0
    return _batch_delete_by_ids(db, "association_edges", ids, id_col="id")


# ---------------------------------------------------------------------------
# Dry-run estimates (read-only; uses a direct connection to avoid side effects)
# ---------------------------------------------------------------------------

def _dry_run_counts(
    db_path: Path,
    profile_id: str,
    cap_degree: bool,
) -> dict:
    """Return estimated removal counts without modifying anything."""
    counts: dict = {
        "orphans_removed": 0,
        "supersedes_collapsed": 0,
        "self_loops_removed": 0,
        "duplicates_removed": 0,
        "hub_edges_removed": 0,
        "association_orphans_removed": 0,
    }
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute(
            """
            SELECT COUNT(*) AS cnt FROM graph_edges
            WHERE profile_id = ?
              AND (
                (source_id NOT IN (SELECT fact_id FROM atomic_facts)
                 AND source_id NOT IN (SELECT entity_id FROM canonical_entities))
                OR
                (target_id NOT IN (SELECT fact_id FROM atomic_facts)
                 AND target_id NOT IN (SELECT entity_id FROM canonical_entities))
              )
            """,
            (profile_id,),
        )
        counts["orphans_removed"] = c.fetchone()["cnt"]

        c.execute(
            "SELECT COUNT(*) AS cnt FROM graph_edges "
            "WHERE source_id = target_id AND profile_id = ?",
            (profile_id,),
        )
        counts["self_loops_removed"] = c.fetchone()["cnt"]

        c.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM graph_edges WHERE profile_id = ?) -
                (SELECT COUNT(*) FROM (
                    SELECT source_id, target_id, edge_type
                    FROM graph_edges WHERE profile_id = ?
                    GROUP BY source_id, target_id, edge_type
                )) AS cnt
            """,
            (profile_id, profile_id),
        )
        counts["duplicates_removed"] = max(c.fetchone()["cnt"] or 0, 0)

        if cap_degree:
            c.execute(
                """
                SELECT COUNT(*) AS cnt FROM (
                    SELECT edge_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY source_id ORDER BY weight DESC
                           ) AS out_rn,
                           ROW_NUMBER() OVER (
                               PARTITION BY target_id ORDER BY weight DESC
                           ) AS in_rn
                    FROM graph_edges
                    WHERE profile_id = ?
                ) WHERE out_rn > ? OR in_rn > ?
                """,
                (profile_id, _MAX_DEGREE_PER_NODE, _MAX_DEGREE_PER_NODE),
            )
            counts["hub_edges_removed"] = c.fetchone()["cnt"]

        conn.close()
    except Exception as exc:
        logger.warning("dry-run counts failed: %s", exc)
    return counts


def _remove_orphan_association_edges(
    c: sqlite3.Cursor,
    profile_id: str,
    dry_run: bool,
) -> int:
    """gi-04: remove association_edges whose source/target fact no longer
    exists in atomic_facts.

    prune_graph historically only touched graph_edges despite its docstring
    claiming "all graph pruning". Hard-deleted facts leave orphaned
    association_edges (the FK cascade only fires under FK-on connections),
    which spreading_activation then has to scan. Returns rows removed.
    """
    where = (
        "profile_id = ? AND ("
        "source_fact_id NOT IN (SELECT fact_id FROM atomic_facts WHERE profile_id = ?) "
        "OR target_fact_id NOT IN (SELECT fact_id FROM atomic_facts WHERE profile_id = ?))"
    )
    c.execute(f"SELECT COUNT(*) AS cnt FROM association_edges WHERE {where}",
              (profile_id, profile_id, profile_id))
    n = c.fetchone()["cnt"]
    if dry_run or not n:
        return n
    c.execute(f"DELETE FROM association_edges WHERE {where}",
              (profile_id, profile_id, profile_id))
    return c.rowcount


def _remove_orphan_edges(
    c: sqlite3.Cursor,
    profile_id: str,
    dry_run: bool,
) -> int:
    """Remove edges where source OR target no longer exists in facts/entities.

    Scoped to profile_id. Checks both source_id and target_id.
    """
    orphan_sql = """
        SELECT COUNT(*) as cnt FROM graph_edges
        WHERE profile_id = ?
          AND (
            (source_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND source_id NOT IN (SELECT entity_id FROM canonical_entities))
            OR
            (target_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND target_id NOT IN (SELECT entity_id FROM canonical_entities))
          )
    """

    if dry_run:
        c.execute(orphan_sql, (profile_id,))
        return c.fetchone()["cnt"]

    c.execute("""
        DELETE FROM graph_edges
        WHERE profile_id = ?
          AND (
            (source_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND source_id NOT IN (SELECT entity_id FROM canonical_entities))
            OR
            (target_id NOT IN (SELECT fact_id FROM atomic_facts)
             AND target_id NOT IN (SELECT entity_id FROM canonical_entities))
          )
    """, (profile_id,))
    return c.rowcount


def _remove_self_loops(
    c: sqlite3.Cursor,
    profile_id: str,
    dry_run: bool,
) -> int:
    """Remove edges where source equals target. Scoped to profile_id."""
    if dry_run:
        c.execute(
            "SELECT COUNT(*) as cnt FROM graph_edges "
            "WHERE source_id = target_id AND profile_id = ?",
            (profile_id,),
        )
        return c.fetchone()["cnt"]

    c.execute(
        "DELETE FROM graph_edges WHERE source_id = target_id AND profile_id = ?",
        (profile_id,),
    )
    return c.rowcount


def _remove_duplicate_edges(
    c: sqlite3.Cursor,
    profile_id: str,
    dry_run: bool,
) -> int:
    """Remove duplicate edges (same source+target+type), keeping highest weight.

    Uses correlated subquery for SQLite 3.22+ compatibility (no window functions).
    """
    if dry_run:
        # Count actual edges to be deleted (total - groups = excess edges)
        c.execute("""
            SELECT
                (SELECT COUNT(*) FROM graph_edges WHERE profile_id = ?) -
                (SELECT COUNT(*) FROM (
                    SELECT source_id, target_id, edge_type
                    FROM graph_edges WHERE profile_id = ?
                    GROUP BY source_id, target_id, edge_type
                )) as cnt
        """, (profile_id, profile_id))
        return max(c.fetchone()["cnt"], 0)

    # Keep the edge with highest weight per (source, target, type).
    # Portable: no ROW_NUMBER() OVER, works on SQLite 3.22+.
    c.execute("""
        DELETE FROM graph_edges
        WHERE profile_id = ?
          AND edge_id NOT IN (
            SELECT edge_id FROM graph_edges ge1
            WHERE profile_id = ?
              AND weight = (
                SELECT MAX(weight) FROM graph_edges ge2
                WHERE ge2.source_id = ge1.source_id
                  AND ge2.target_id = ge1.target_id
                  AND ge2.edge_type = ge1.edge_type
                  AND ge2.profile_id = ge1.profile_id
              )
            GROUP BY source_id, target_id, edge_type
          )
    """, (profile_id, profile_id))
    return c.rowcount


def _collapse_supersedes_chains(
    c: sqlite3.Cursor,
    profile_id: str,
    dry_run: bool,
) -> int:
    """Collapse supersedes chains: if A supersedes B and B supersedes C,
    remove B→C edge AND create A→C shortcut edge.

    Preserves reachability: A can still reach C via the new direct edge.
    """
    c.execute("""
        SELECT e1.edge_id as e1_id, e1.source_id as a, e1.target_id as b,
               e1.weight as e1_weight,
               e2.edge_id as e2_id, e2.target_id as c
        FROM graph_edges e1
        JOIN graph_edges e2 ON e1.target_id = e2.source_id
        WHERE e1.edge_type = 'supersedes'
          AND e2.edge_type = 'supersedes'
          AND e1.profile_id = ?
          AND e2.profile_id = ?
        LIMIT ?
    """, (profile_id, profile_id, _CHAIN_BATCH_LIMIT))

    chains = c.fetchall()
    if not chains:
        return 0

    if len(chains) >= _CHAIN_BATCH_LIMIT:
        logger.warning(
            "Supersedes chain collapse hit limit (%d). "
            "More chains may exist — will process in next cycle.",
            _CHAIN_BATCH_LIMIT,
        )

    if dry_run:
        return len(chains)

    # Collect IDs for batch operations
    delete_ids: list[str] = []
    insert_rows: list[tuple] = []
    now = datetime.now(UTC).isoformat()

    for chain in chains:
        a_id = chain["a"]
        c_id = chain["c"]
        e2_id = chain["e2_id"]
        weight = chain["e1_weight"] or 1.0

        delete_ids.append(e2_id)

        # Create A→C shortcut edge (preserves reachability)
        new_edge_id = uuid.uuid4().hex[:16]
        insert_rows.append((
            new_edge_id, profile_id, a_id, c_id,
            "supersedes", weight, now,
        ))

    # Batch DELETE: remove all B→C intermediate edges
    for i in range(0, len(delete_ids), 500):
        batch = delete_ids[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        c.execute(
            f"DELETE FROM graph_edges WHERE edge_id IN ({placeholders})",
            batch,
        )

    # Batch INSERT: add all A→C shortcut edges
    c.executemany(
        "INSERT OR IGNORE INTO graph_edges "
        "(edge_id, profile_id, source_id, target_id, edge_type, weight, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )

    return len(delete_ids)


def _cap_node_degree(
    c: sqlite3.Cursor,
    profile_id: str,
    max_degree: int,
    dry_run: bool,
) -> int:
    """Remove low-weight edges from hub nodes (nodes with degree > max_degree).

    v3.4.59: At 17K facts, popular entities (SLM, Claude, AgentAssert) caused
    1.4M+ entity edges — avg 121 per node, some nodes with 5000+. SA fan-out
    is capped at 30/node during traversal, so edges beyond max_degree add zero
    recall signal while making every graph query scan millions of rows.

    Algorithm (single-pass window function — no Python loops):
      1. ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY weight DESC) ranks
         every edge per node in one full table scan.
      2. Excess edge IDs are collected in a reusable temp table.
      3. A single DELETE statement removes them; rowcount is returned.
      4. The temp table is created once and cleared via DELETE (not DROP) —
         DROP TABLE acquires an EXCLUSIVE lock that conflicts with concurrent
         writers, causing "database is locked".  Using CREATE...IF NOT EXISTS
         plus DELETE FROM avoids that conflict while preserving rowcount.
    """
    # gi-03: cap BOTH out-degree (PARTITION BY source_id) AND in-degree
    # (PARTITION BY target_id). Previously only out-degree was capped, so hub
    # nodes accumulated unbounded in-degree (observed up to 1457), inflating
    # entity-channel fan-in cost. An edge is removed if it exceeds max_degree
    # in EITHER direction (low-weight to both its endpoints). Computed in one
    # window-function pass, no Python loops.
    if dry_run:
        c.execute(
            """
            SELECT COUNT(*) as cnt FROM (
                SELECT edge_id,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY weight DESC) as out_rn,
                       ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY weight DESC) as in_rn
                FROM graph_edges
                WHERE profile_id = ?
            ) WHERE out_rn > ? OR in_rn > ?
            """,
            (profile_id, max_degree, max_degree),
        )
        excess = c.fetchone()["cnt"]
        logger.info(
            "(dry-run) _cap_node_degree: ~%d edges would be removed (max_degree=%d, in+out)",
            excess, max_degree,
        )
        return excess

    # CREATE IF NOT EXISTS + DELETE FROM instead of DROP + CREATE.
    # DROP TABLE acquires EXCLUSIVE which conflicts with concurrent writers.
    # CREATE IF NOT EXISTS is idempotent; DELETE FROM clears prior contents.
    c.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _slm_cap_del (edge_id TEXT PRIMARY KEY)"
    )
    c.execute("DELETE FROM _slm_cap_del")
    c.execute(
        """
        INSERT OR IGNORE INTO _slm_cap_del (edge_id)
        SELECT edge_id FROM (
            SELECT edge_id,
                   ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY weight DESC) as out_rn,
                   ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY weight DESC) as in_rn
            FROM graph_edges
            WHERE profile_id = ?
        ) WHERE out_rn > ? OR in_rn > ?
        """,
        (profile_id, max_degree, max_degree),
    )

    c.execute(
        """
        DELETE FROM graph_edges
        WHERE profile_id = ?
          AND edge_id IN (SELECT edge_id FROM _slm_cap_del)
        """,
        (profile_id,),
    )
    deleted = c.rowcount

    logger.info(
        "_cap_node_degree: deleted %d low-weight edges (max_degree=%d, in+out capped)",
        deleted, max_degree,
    )
    return deleted
