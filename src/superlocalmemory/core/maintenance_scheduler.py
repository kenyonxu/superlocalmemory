# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Background Maintenance Scheduler.

V3.3.13: Periodically triggers Langevin/Ebbinghaus/Sheaf maintenance
so users don't need to call run_maintenance manually.

Configurable interval via ForgettingConfig.scheduler_interval_minutes.
Defaults to 30 min. Optional forgetting/math work follows
``config.forgetting.enabled``; tier evaluation and bounded housekeeping do not.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger(__name__)


class MaintenanceScheduler:
    """Background scheduler for periodic math maintenance.

    Runs Langevin/Sheaf/Fisher maintenance at configurable intervals.
    Thread-safe. Auto-stops on garbage collection or explicit stop().
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: SLMConfig,
        profile_id: str = "default",
        embedder: object | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._profile_id = profile_id
        # v3.8.2 self-heal: when provided, periodic maintenance backfills
        # NULL embeddings so a DB stays fully queryable over time even if
        # facts were stored while the embedder was unavailable. Runs
        # independently of forgetting.enabled (see _run).
        self._embedder = embedder
        self._timer: threading.Timer | None = None
        self._running = False
        self._interval = config.forgetting.scheduler_interval_minutes * 60.0

    def start(self) -> None:
        """Start the periodic scheduler. Idempotent."""
        if self._running:
            return
        self._running = True
        self._schedule_next()
        # v3.8.5: one-shot activation-cache GC ~90s after boot so an upgrade
        # backlog (observed 83k expired rows) clears promptly instead of waiting
        # a full interval — delayed past boot warmup so it never competes for
        # the write lock during the startup window.
        self._initial_gc_timer = threading.Timer(90.0, self._initial_cache_gc)
        self._initial_gc_timer.daemon = True
        self._initial_gc_timer.start()
        # An upgrade arrives with whatever backlog the previous version left.
        # Waiting a full interval for the first graph-metrics pass would mean
        # half an hour of ranking memories as though they had no position in the
        # graph, on exactly the store that just gained the fix. Staggered behind
        # the cache GC so the two never contend for the write lock.
        self._initial_metrics_timer = threading.Timer(
            150.0, self._initial_graph_metrics,
        )
        self._initial_metrics_timer.daemon = True
        self._initial_metrics_timer.start()
        logger.info(
            "Maintenance scheduler started (interval=%dm)",
            self._config.forgetting.scheduler_interval_minutes,
        )

    def _initial_cache_gc(self) -> None:
        """Best-effort one-shot activation-cache GC shortly after boot."""
        if not self._running:
            return
        try:
            deleted = self._db.cleanup_activation_cache()
            if deleted > 0:
                logger.info(
                    "Activation-cache GC (startup): %d expired rows removed",
                    deleted,
                )
        except Exception as exc:
            logger.debug("Startup activation-cache GC skipped: %s", exc)

    def _initial_graph_metrics(self) -> None:
        """One-shot catch-up so an upgrade does not rank on stale metrics."""
        if not self._running:
            return
        try:
            from superlocalmemory.core.graph_metrics import (
                compute_graph_metrics,
                metrics_are_stale,
            )
            for profile_id in self._profile_ids():
                stale, why = metrics_are_stale(self._db, profile_id)
                if not stale:
                    continue
                report = compute_graph_metrics(self._db, profile_id)
                if report.ok:
                    logger.info(
                        "Graph metrics at startup (%s): %s", why, report.summary(),
                    )
                else:
                    logger.warning("Graph metrics at startup: %s", report.summary())
        except Exception as exc:
            logger.debug("Startup graph metrics skipped: %s", exc)

    def stop(self) -> None:
        """Stop the scheduler. Idempotent."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        _gc_timer = getattr(self, "_initial_gc_timer", None)
        if _gc_timer is not None:
            _gc_timer.cancel()
            self._initial_gc_timer = None
        _metrics_timer = getattr(self, "_initial_metrics_timer", None)
        if _metrics_timer is not None:
            _metrics_timer.cancel()
            self._initial_metrics_timer = None
        logger.info("Maintenance scheduler stopped")

    def _schedule_next(self) -> None:
        """Schedule the next maintenance run."""
        if not self._running:
            return
        self._timer = threading.Timer(self._interval, self._run)
        self._timer.daemon = True
        self._timer.start()

    def _run(self) -> None:
        """Execute maintenance + auto-backup check, then schedule next run."""
        if not self._running:
            return
        # v3.8.2 self-heal: bounded NULL-embedding backfill runs every cycle
        # INDEPENDENTLY of forgetting.enabled and across ALL profiles — a fact
        # stored while the embedder was down must become queryable again without
        # the user touching anything. Idempotent + bounded (200/pass) so it
        # converges quietly and is a no-op once coverage is complete.
        if self._embedder is not None:
            try:
                from superlocalmemory.storage.embedding_migrator import (
                    backfill_missing_embeddings,
                )
                r = backfill_missing_embeddings(
                    self._config, self._db, self._embedder,
                    limit=50, all_profiles=True,
                )
                if r.get("embedded"):
                    logger.info(
                        "Self-heal backfill: %d embedded, %d remaining",
                        r["embedded"], r["remaining_null"],
                    )
            except Exception as exc:
                logger.debug("Self-heal backfill skipped: %s", exc)

        for profile_id in self._profile_ids():
            if self._config.forgetting.enabled:
                try:
                    from superlocalmemory.core.maintenance import run_maintenance
                    counts = run_maintenance(self._db, self._config, profile_id)
                    logger.info(
                        "Scheduled maintenance complete for %s: %s",
                        profile_id,
                        counts,
                    )
                except Exception as exc:
                    logger.warning(
                        "Scheduled maintenance failed for %s: %s",
                        profile_id,
                        exc,
                    )

            # V3.4.11: Graph pruning (remove orphan edges)
            # v3.8.4-G: thread GraphPruningConfig params so dashboard changes
            # persist and take effect without a daemon restart.
            try:
                from superlocalmemory.core.graph_pruner import prune_graph
                gp = self._config.graph_pruning
                if not gp.enabled:
                    logger.debug(
                        "Graph pruning disabled by config for %s — skipping",
                        profile_id,
                    )
                else:
                    # Fix A: pass DatabaseManager directly → writes serialised through _lock
                    prune_stats = prune_graph(
                        self._db,
                        profile_id,
                        max_degree=gp.max_degree_per_node,
                        min_edge_weight=gp.min_edge_weight,
                    )
                    removed = prune_stats["total_before"] - prune_stats["total_after"]
                    if removed > 0:
                        logger.info(
                            "Graph pruning for %s: %d edges removed", profile_id, removed
                        )
            except AttributeError:
                # Older SLMConfig without graph_pruning field (upgrade safety)
                from superlocalmemory.core.graph_pruner import prune_graph
                prune_stats = prune_graph(self._db, profile_id)
                removed = prune_stats["total_before"] - prune_stats["total_after"]
                if removed > 0:
                    logger.info("Graph pruning for %s: %d edges removed", profile_id, removed)
            except Exception as exc:
                logger.debug("Graph pruning skipped for %s: %s", profile_id, exc)

            # Pruning the graph orphans the lineage of every edge it removed,
            # and nothing had ever deleted from that table — on a real store it
            # had grown to 39% rows describing edges that no longer existed.
            # This runs immediately after so the rows the pass just orphaned are
            # collected in the same pass.
            try:
                from superlocalmemory.storage.lineage_retention import (
                    prune_orphan_lineage,
                )
                report = prune_orphan_lineage(self._db, profile_id=profile_id)
                if report.total:
                    logger.info(
                        "Lineage retention for %s: %d row(s) removed (%s)",
                        profile_id, report.total, report.deleted,
                    )
            except Exception as exc:
                logger.debug("Lineage retention skipped for %s: %s", profile_id, exc)

            # Structural metrics. Recall multiplies a candidate's activation by
            # its PageRank at every hop and biases it toward its query seeds'
            # communities, and both numbers live in fact_importance -- so a
            # memory missing from that table is found by the walk and then
            # ranked as though it had no position in the graph.
            #
            # Nothing scheduled this. It ran only when a consolidation happened
            # to fire or someone called the HTTP endpoint by hand, and on the
            # author's store that meant one run in nine days: 1,036 of 4,034
            # visible memories had no score and no community, and the newest
            # four days of memories had none at all. This runs after pruning so
            # it describes the graph that pruning left behind.
            try:
                from superlocalmemory.core.graph_metrics import (
                    compute_graph_metrics,
                    metrics_are_stale,
                )
                stale, why = metrics_are_stale(self._db, profile_id)
                if stale:
                    backend = None
                    try:
                        from superlocalmemory.core.backend_orchestrator import (
                            get_orchestrator,
                        )
                        orchestrator = get_orchestrator()
                        if orchestrator is not None:
                            backend = orchestrator.get_graph_backend()
                    except Exception:  # noqa: BLE001 -- in-process is the default anyway
                        backend = None
                    report = compute_graph_metrics(
                        self._db, profile_id, backend=backend,
                    )
                    if report.ok:
                        logger.info("Graph metrics (%s): %s", why, report.summary())
                    else:
                        logger.warning("Graph metrics: %s", report.summary())
                else:
                    logger.debug("Graph metrics up to date for %s", profile_id)
            except Exception as exc:
                logger.warning(
                    "Graph metrics skipped for %s: %s", profile_id, exc,
                )

            # Re-read what is filed as a plan. The one-time pass runs as a
            # migration; the rule it uses keeps getting sharper, and a completed
            # migration is never replayed — so without this the store drifts
            # further from the rule with every release and nothing repairs it.
            # The pass is a pure function of the text and idempotent, so this is
            # a no-op once the store has converged.
            try:
                from superlocalmemory.storage.migrations import (
                    M048_upcoming_holds_only_what_is_upcoming as _reclassify,
                )
                _conn = getattr(self._db, "_conn", None)
                if _conn is None and hasattr(self._db, "connection"):
                    _conn = self._db.connection
                if _conn is not None:
                    _reclassify.apply(_conn)
            except Exception as exc:
                logger.debug(
                    "re-reading what is filed as a plan skipped for %s: %s",
                    profile_id, exc,
                )

            # Lifecycle evaluation must cover every stored profile, not only
            # whichever profile was active when the engine started.
            try:
                from superlocalmemory.core.tier_manager import evaluate_tiers
                stats = evaluate_tiers(self._db, profile_id)
                demoted = stats["demoted_to_warm"] + stats["demoted_to_cold"] + stats["demoted_to_archive"]
                if demoted > 0:
                    logger.info("Tier evaluation for %s: %d facts demoted", profile_id, demoted)
            except Exception as exc:
                logger.debug("Tier evaluation skipped for %s: %s", profile_id, exc)

            # v3.6.6 F-5: Daily core-block recompile with hygiene.
            try:
                from superlocalmemory.core.block_hygiene import _recompile_core_blocks
                _recompile_core_blocks(self._db, self._config, profile_id)
            except Exception as exc:
                logger.debug("Core-block recompile skipped for %s: %s", profile_id, exc)

        # Retention. Three tables had a pruner each, written and wired
        # separately; the fourth unbounded table was found by reading a
        # disk-usage report and the fifth by reading the fourth. The policy for
        # every append-shaped table now lives in one registry and this enforces
        # all of them, so a table added without a policy is something the test
        # suite can see rather than something a person has to remember.
        #
        # Once per cycle, not per profile: every rule is keyed either on a row's
        # own age or on whether its referent still exists, and neither is
        # profile-scoped. Placed after the per-profile work so it sweeps rows
        # that pass orphaned -- pruning the graph and demoting tiers is what
        # leaves a lineage or temporal row without a referent.
        try:
            from superlocalmemory.storage.retention_policy import run_retention
            with self._db.raw_connection() as conn:
                removed = run_retention(conn)
            if removed:
                logger.info(
                    "Retention: %s",
                    ", ".join(
                        f"{table} -{count}" for table, count in sorted(removed.items())
                    ),
                )
        except Exception as exc:
            logger.warning("Retention pass skipped: %s", exc)

        # V3.4.10: Check if auto-backup is due
        try:
            from superlocalmemory.infra.backup import BackupManager
            manager = BackupManager(db_path=self._db.db_path)
            filename = manager.check_and_backup()
            if filename:
                logger.info("Auto-backup created: %s", filename)
                self._sync_cloud_destinations(manager)
        except Exception as exc:
            logger.debug("Auto-backup check skipped: %s", exc)

        try:
            from superlocalmemory.cli.pending_store import cleanup_stale
            stats = cleanup_stale()
            if stats["total"] > 0:
                logger.info("Pending cleanup: %s", stats)
        except Exception as exc:
            logger.debug("Pending cleanup skipped: %s", exc)

        # v3.8.5: GC the spreading-activation result cache. Neither cleanup path
        # was ever wired in, so activation_cache grew without bound (observed
        # 83k expired rows, oldest ~3.5 months). Batched + DB-wide (expiry is
        # not profile-scoped), so it runs once per cycle rather than per profile.
        try:
            deleted = self._db.cleanup_activation_cache()
            if deleted > 0:
                logger.info("Activation-cache GC: %d expired rows removed", deleted)
        except Exception as exc:
            logger.debug("Activation-cache GC skipped: %s", exc)

        self._schedule_next()

    def _profile_ids(self) -> tuple[str, ...]:
        """Return every persisted profile with a deterministic fallback."""
        try:
            rows = self._db.execute(
                "SELECT profile_id FROM profiles ORDER BY profile_id",
                (),
            )
            profiles = tuple(
                dict.fromkeys(str(row["profile_id"]) for row in rows if row["profile_id"])
            )
            if profiles:
                return profiles
        except Exception as exc:
            logger.debug("Profile enumeration failed: %s", exc)
        return (self._profile_id,)

    def _sync_cloud_destinations(self, manager: object) -> None:
        """Push latest backup to configured cloud destinations."""
        try:
            from superlocalmemory.infra.cloud_backup import sync_all_destinations
            sync_all_destinations(self._db.db_path)
        except ImportError:
            pass  # cloud_backup module not available yet
        except Exception as exc:
            logger.warning("Cloud sync failed (non-critical): %s", exc)

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
