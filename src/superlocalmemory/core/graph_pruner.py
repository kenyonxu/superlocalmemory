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

logger = logging.getLogger("superlocalmemory.graph_pruner")

_CHAIN_BATCH_LIMIT = 10_000


def prune_graph(
    db_path: str | Path,
    profile_id: str = "default",
    dry_run: bool = False,
) -> dict:
    """Run all graph pruning strategies for a specific profile.

    Returns stats dict with counts for each strategy.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row

    stats = {
        "orphans_removed": 0,
        "supersedes_collapsed": 0,
        "self_loops_removed": 0,
        "duplicates_removed": 0,
        "total_before": 0,
        "total_after": 0,
    }

    try:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) as cnt FROM graph_edges WHERE profile_id = ?",
            (profile_id,),
        )
        stats["total_before"] = c.fetchone()["cnt"]

        start = time.time()

        # Explicit transaction for atomicity
        c.execute("BEGIN")

        stats["orphans_removed"] = _remove_orphan_edges(c, profile_id, dry_run)
        stats["self_loops_removed"] = _remove_self_loops(c, profile_id, dry_run)
        stats["duplicates_removed"] = _remove_duplicate_edges(c, profile_id, dry_run)
        stats["supersedes_collapsed"] = _collapse_supersedes_chains(
            c, profile_id, dry_run,
        )

        if dry_run:
            c.execute("ROLLBACK")
        else:
            c.execute("COMMIT")

        c.execute(
            "SELECT COUNT(*) as cnt FROM graph_edges WHERE profile_id = ?",
            (profile_id,),
        )
        stats["total_after"] = c.fetchone()["cnt"]

        elapsed = time.time() - start
        total_removed = stats["total_before"] - stats["total_after"]
        pct = round(total_removed / max(stats["total_before"], 1) * 100, 1)

        prefix = "(dry-run) " if dry_run else ""
        logger.info(
            "%sGraph pruning: removed %d edges (%.1f%%) in %.1fs — "
            "orphans=%d, supersedes=%d, self_loops=%d, duplicates=%d",
            prefix, total_removed, pct, elapsed,
            stats["orphans_removed"], stats["supersedes_collapsed"],
            stats["self_loops_removed"], stats["duplicates_removed"],
        )

    except Exception as exc:
        logger.error("Graph pruning failed: %s", exc, exc_info=True)
        stats["error"] = str(exc)
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
    finally:
        conn.close()

    return stats


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
