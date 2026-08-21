# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4 — CodeGraph Module

"""GraphStore — thin graph-specific layer over CodeGraphDatabase.

All graph writes go through this layer.  Provides:
- Atomic file replacement  (store_file_nodes_edges)
- Bulk read for in-memory graph building  (get_all_nodes_and_edges)
- File removal  (remove_file)
- Version tracking for cache invalidation
"""

from __future__ import annotations

import logging
from typing import Sequence

from superlocalmemory.code_graph.database import CodeGraphDatabase
from superlocalmemory.code_graph.models import (
    FileRecord,
    GraphEdge,
    GraphNode,
)

logger = logging.getLogger(__name__)


class GraphStore:
    """SQLite persistence layer for graph nodes, edges, and file records.

    Delegates to CodeGraphDatabase but adds higher-level operations
    that Phase 2+ modules depend on (bulk load, atomic replace, version).
    """

    def __init__(self, db: CodeGraphDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def db(self) -> CodeGraphDatabase:
        """Underlying database instance."""
        return self._db

    @property
    def version(self) -> int:
        """Monotonic write-version for cache invalidation."""
        return self._db.version

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store_file_nodes_edges(
        self,
        file_path: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        file_record: FileRecord,
    ) -> None:
        """Atomically replace all data for *file_path*.

        Within a single transaction:
        1. Delete old edges for this file
        2. Delete old nodes for this file
        3. Insert new nodes
        4. Insert new edges
        5. Upsert file record

        Fix B — Defensive pre-filter:
        Before delegating to the DB, drop any edge whose source_node_id or
        target_node_id is absent from *both* the local node set AND the
        database.  This guards the incremental update path (update_code_graph
        → parse_file → here) which does not run the parse_all resolver
        pipeline.  After a full build, parse_all's resolution pass should make
        this a no-op; the filter is a belt-and-braces against partial runs.

        DO NOT fix by disabling the FK — the FK is correct and protects graph
        integrity.
        """
        # ── build valid-id set: local nodes ──────────────────────────────
        #
        # REPLACE semantics: `store_file_parse_results` uses INSERT OR REPLACE.
        # graph_nodes has a UNIQUE constraint on `qualified_name`.  When two
        # nodes share a qualified_name (e.g. a @property getter AND setter, or
        # a function defined twice in source), the LATER insert wins — the
        # EARLIER node is deleted via REPLACE, and ON DELETE CASCADE removes
        # any edges that already referenced it.  We simulate this here: only
        # the LAST node per qualified_name survives; edges referencing the
        # earlier "losers" must be dropped before we reach the DB.
        qn_last: dict[str, GraphNode] = {}
        for n in nodes:
            qn_last[n.qualified_name] = n          # last occurrence wins
        surviving_ids = {n.node_id for n in qn_last.values()}

        # Collect foreign endpoints (not satisfied locally) for a single
        # batch DB check — avoids N+1 queries.
        foreign_ids: set[str] = set()
        for edge in edges:
            if edge.source_node_id not in surviving_ids:
                foreign_ids.add(edge.source_node_id)
            if edge.target_node_id not in surviving_ids:
                foreign_ids.add(edge.target_node_id)

        db_ids: set[str] = set()
        if foreign_ids:
            placeholders = ",".join("?" * len(foreign_ids))
            rows = self._db.execute(
                f"SELECT node_id FROM graph_nodes WHERE node_id IN ({placeholders})",
                tuple(foreign_ids),
            )
            db_ids = {row["node_id"] for row in rows}

        valid_ids = surviving_ids | db_ids

        safe_edges: list[GraphEdge] = []
        dropped = 0
        for edge in edges:
            if edge.source_node_id in valid_ids and edge.target_node_id in valid_ids:
                safe_edges.append(edge)
            else:
                dropped += 1
                logger.debug(
                    "store_file_nodes_edges: dropped dangling edge %s→%s "
                    "for file %s (resolver may not have run on this path)",
                    edge.source_node_id, edge.target_node_id, file_path,
                )
        if dropped:
            logger.debug(
                "store_file_nodes_edges: total %d dangling edge(s) dropped for %s",
                dropped, file_path,
            )

        self._db.store_file_parse_results(
            file_path,
            list(nodes),
            safe_edges,
            file_record,
        )
        logger.debug(
            "Stored %d nodes, %d edges for %s",
            len(nodes), len(safe_edges), file_path,
        )

    def commit_build_batch(
        self,
        batch: list[tuple[str, list[GraphNode], list[GraphEdge], FileRecord]],
    ) -> None:
        """Two-phase bulk commit — order-independent storage for full builds.

        The single-file ``store_file_nodes_edges`` is insertion-order-
        dependent: when a caller file (a.py, has CALLS foo→bar) is stored
        before the callee file (b.py, defines bar), the cross-file CALLS
        edge is dropped because Fix-B's DB existence check for bar.node_id
        fails (b.py has not been stored yet).

        This method fixes the root cause by separating commits into two
        phases, both executed in a single atomic transaction:

        **Phase 1 — all nodes**: for every file in the batch, delete old
        data and insert new nodes.  After Phase 1, every node_id from every
        file in the batch is present in ``graph_nodes``.

        **Phase 2 — all edges**: for every file, validate endpoints (the DB
        existence check now finds callee nodes regardless of file order) and
        insert qualifying edges.

        Design pattern: *Separated Phases*.  Interleaved per-file commits
        (the existing loop) are O(1) transaction boundaries but
        insertion-order-dependent.  Separated phases add one extra pass but
        are fully order-independent.

        Use this for full builds (``build_code_graph``).  Single-file
        updates (``update_code_graph``) continue to use
        ``store_file_nodes_edges`` — the callee nodes from other files are
        already in the DB from the previous build, so no ordering issue.
        """
        if not batch:
            return

        with self._db.transaction():
            # ── Phase 1: delete old data + insert all new nodes ───────────
            #
            # Edges must be deleted before nodes (FK constraint prevents
            # deleting a node that an edge still references).  We delete ALL
            # file edges across the whole batch first, then delete nodes.
            # This avoids cascade surprises when file A's nodes are deleted
            # before file B's edges that target those nodes are cleaned up.
            for fp, _, _, _ in batch:
                self._db.delete_edges_by_file(fp)
            for fp, nodes, _, fr in batch:
                self._db.delete_nodes_by_file(fp)
                for node in nodes:
                    self._db.upsert_node(node)
                self._db.upsert_file_record(fr)

            # ── Phase 2: validate + insert all edges ──────────────────────
            #
            # All node_ids from Phase 1 are now in graph_nodes, so the
            # batch-DB check for cross-file foreign endpoints succeeds
            # regardless of which file was stored first.
            total_dropped = 0
            for fp, nodes, edges, _ in batch:
                # Simulate INSERT OR REPLACE dedup: only the last node per
                # qualified_name survives; edges referencing earlier losers
                # must be pre-dropped (mirrors store_file_nodes_edges logic).
                qn_last: dict[str, GraphNode] = {}
                for n in nodes:
                    qn_last[n.qualified_name] = n
                surviving_ids = {n.node_id for n in qn_last.values()}

                # Batch-check foreign endpoints against DB (avoids N+1).
                foreign_ids: set[str] = set()
                for edge in edges:
                    if edge.source_node_id not in surviving_ids:
                        foreign_ids.add(edge.source_node_id)
                    if edge.target_node_id not in surviving_ids:
                        foreign_ids.add(edge.target_node_id)

                db_ids: set[str] = set()
                if foreign_ids:
                    placeholders = ",".join("?" * len(foreign_ids))
                    rows = self._db.execute(
                        f"SELECT node_id FROM graph_nodes "
                        f"WHERE node_id IN ({placeholders})",
                        tuple(foreign_ids),
                    )
                    db_ids = {row["node_id"] for row in rows}

                valid_ids = surviving_ids | db_ids
                dropped = 0
                for edge in edges:
                    if (
                        edge.source_node_id in valid_ids
                        and edge.target_node_id in valid_ids
                    ):
                        self._db.upsert_edge(edge)
                    else:
                        dropped += 1
                        logger.debug(
                            "commit_build_batch: dropped dangling edge "
                            "%s→%s for %s",
                            edge.source_node_id, edge.target_node_id, fp,
                        )
                if dropped:
                    total_dropped += dropped
                    logger.debug(
                        "commit_build_batch: %d dangling edge(s) dropped for %s",
                        dropped, fp,
                    )

        if total_dropped:
            logger.debug(
                "commit_build_batch: total %d dangling edges dropped across "
                "batch of %d files",
                total_dropped, len(batch),
            )
        logger.debug("commit_build_batch: committed %d files", len(batch))

    def remove_file(self, file_path: str) -> None:
        """Remove all graph data for *file_path*.

        Deletes nodes (cascade → edges via FK), edges sourced from this
        file, and the file record.  All within a transaction.
        """
        with self._db.transaction():
            self._db.delete_edges_by_file(file_path)
            self._db.delete_nodes_by_file(file_path)
            self._db.delete_file_record(file_path)
        logger.debug("Removed all data for %s", file_path)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all_nodes_and_edges(
        self,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Load every node and edge — used by GraphEngine.build_graph().

        Returns (nodes, edges) as plain lists.
        """
        nodes = self._db.get_all_nodes()
        edges = self._db.get_all_edges()
        return nodes, edges

    def get_nodes_by_file(self, file_path: str) -> list[GraphNode]:
        """All nodes in *file_path*, ordered by line_start."""
        return self._db.get_nodes_by_file(file_path)

    def get_node(self, node_id: str) -> GraphNode | None:
        """Single node by ID."""
        return self._db.get_node(node_id)

    def get_file_record(self, file_path: str) -> FileRecord | None:
        """File record by path."""
        return self._db.get_file_record(file_path)

    def get_all_file_records(self) -> list[FileRecord]:
        """All tracked file records."""
        return self._db.get_all_file_records()

    # ------------------------------------------------------------------
    # Dependent tracing (used by IncrementalUpdater)
    # ------------------------------------------------------------------

    def find_dependents(self, file_path: str) -> set[str]:
        """Return file paths that have edges *targeting* nodes in *file_path*.

        Looks for IMPORTS, CALLS, INHERITS, DEPENDS_ON edges whose
        target lives in *file_path* but whose source is in a *different* file.
        """
        rows = self._db.execute(
            """
            SELECT DISTINCT ge.file_path
            FROM graph_edges ge
            JOIN graph_nodes gn_target
                ON ge.target_node_id = gn_target.node_id
            WHERE gn_target.file_path = ?
              AND ge.file_path != ?
            """,
            (file_path, file_path),
        )
        return {row["file_path"] for row in rows}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Delegate to DB stats."""
        return self._db.get_stats()
