# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory | https://qualixar.com

"""Bridge pass — runs the code↔memory bridge as background maintenance.

WHY THIS MODULE EXISTS
----------------------
The bridge was authored to run from ``BridgeEventListeners.on_memory_stored``,
i.e. once per ``memory.stored`` event. ``EventBus._notify_listeners`` calls
listeners **synchronously on the emitting thread**, so that design puts entity
resolution, enrichment and Hebbian linking inside every single ``remember``.
The owner's constraint for this release is explicit: remember and recall timing
must not move. So the memory-stored listener is gone and the work happens here,
in the same background pass that already runs ``consolidate_facts``.

WHAT THIS PASS TOUCHES
----------------------
Writes to ``code_graph.db`` only:
  * ``code_memory_links``            — EntityResolver output
  * ``code_memory_links.enriched_description`` — FactEnricher output

Recall never opens ``code_graph.db`` — verified: nothing under ``retrieval/`` or
``core/`` references ``code_memory_links`` or ``CodeGraphDatabase``. That makes
this half of the bridge recall-neutral by construction rather than by
measurement.

DELIBERATELY NOT HERE: Hebbian association edges. ``HebbianLinker`` produces
edges for ``association_edges`` in **memory.db**, which
``retrieval/spreading_activation.py`` reads via a UNION with ``graph_edges``.
Every such edge is an extra neighbour recall must traverse and changes which
memories come back — not just how fast. That cannot be made safe by moving it
into this pass, so it is deferred to its own release, where the edge volume at
production scale can be measured against the recall baseline first. No writer for
it ships here; ``bridge/hebbian_linker.py`` remains unwired on purpose.

IDEMPOTENCE
-----------
A watermark in ``graph_metadata`` records the ``created_at`` of the newest fact
processed. Re-running the pass processes only facts newer than that, so a
maintenance cycle every few minutes does not rescan the whole store. Links use
``INSERT OR REPLACE`` on a deterministic key, so reprocessing a fact cannot
duplicate its links.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from superlocalmemory.code_graph.database import CodeGraphDatabase

logger = logging.getLogger(__name__)

#: Watermark key in graph_metadata.
_WATERMARK_KEY = "bridge.last_fact_created_at"

#: Facts examined in a single pass. Bounds the pass so a first run on a large
#: store cannot occupy the maintenance thread indefinitely; the watermark means
#: the next cycle resumes where this one stopped.
MAX_FACTS_PER_PASS = 500

#: Links kept for one fact, highest confidence first. A single file-path mention
#: matches every node in that file — "the parser in code_graph/parser.py was
#: dropping edges" produced 17 links at confidence 0.6-0.8, against one link at
#: 0.95 for a backticked function name. Without a bound the broad matches bury
#: the precise ones in the UI and hand a large node set to the Hebbian pass.
MAX_LINKS_PER_FACT = 10


def _fact_rows(
    memory_db: Any,
    profile_id: str,
    since: str | None,
    limit: int,
) -> list[tuple[str, str, str]]:
    """Return (fact_id, content, created_at) for facts newer than *since*.

    Ordered by ``created_at`` so the watermark advances monotonically even when
    the pass stops at ``limit``.
    """
    sql = (
        "SELECT fact_id, content, created_at FROM atomic_facts "
        "WHERE profile_id = ? AND content IS NOT NULL AND content != '' "
    )
    # M011 (archive_status) is a DEFERRED migration, so the column is absent on
    # a database where it has not run yet. DatabaseManager._has_archive_status
    # exists for exactly this and its docstring is explicit: "callers must not
    # filter on a column that may not exist." Filtering unconditionally made
    # this query raise "no such column" on any fresh install, which the caller's
    # except swallowed into a warning and zero links — the bridge would simply
    # never have run for a new user.
    try:
        has_archive = memory_db._has_archive_status()
    except Exception:  # pragma: no cover - helper absent on an unusual manager
        has_archive = False
    if has_archive:
        sql += "AND (archive_status IS NULL OR archive_status = '') "

    params: list[Any] = [profile_id]
    if since:
        sql += "AND created_at > ? "
        params.append(since)
    sql += "ORDER BY created_at ASC LIMIT ?"
    params.append(limit)

    # DatabaseManager.execute serves both reads and writes (see
    # core/maintenance.py, which uses it for each). It returns sqlite3.Row;
    # index by position so a plain-tuple factory also works.
    rows = memory_db.execute(sql, tuple(params))
    return [(r[0], r[1], r[2]) for r in rows]


def run_bridge_pass(
    memory_db: Any,
    code_graph_db: CodeGraphDatabase,
    profile_id: str,
    *,
    max_facts: int = MAX_FACTS_PER_PASS,
) -> dict[str, int]:
    """Resolve code mentions in new facts and enrich the resulting links.

    Returns counts. Never raises — the caller is background maintenance and a
    bridge failure must not abort the rest of the cycle.
    """
    counts = {"facts_scanned": 0, "links_created": 0, "enriched": 0}

    try:
        from superlocalmemory.code_graph.bridge.entity_resolver import EntityResolver
        from superlocalmemory.code_graph.bridge.fact_enricher import FactEnricher
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("bridge pass unavailable: %s", exc)
        return counts

    # Nothing to match against — skip before touching memory.db at all.
    stats = code_graph_db.get_stats()
    if not stats.get("nodes"):
        logger.debug("bridge pass: code graph is empty, nothing to resolve against")
        return counts

    watermark = code_graph_db.get_metadata(_WATERMARK_KEY)
    try:
        rows = _fact_rows(memory_db, profile_id, watermark, max_facts)
    except Exception as exc:
        logger.warning("bridge pass could not read facts: %s", exc)
        return counts

    if not rows:
        return counts

    resolver = EntityResolver(code_graph_db)
    enricher = FactEnricher(code_graph_db)
    newest = watermark

    for fact_id, content, created_at in rows:
        counts["facts_scanned"] += 1
        newest = created_at if newest is None or created_at > newest else newest
        try:
            links = resolver.resolve(content, fact_id, max_links=MAX_LINKS_PER_FACT)
        except Exception as exc:
            logger.debug("bridge resolve failed for %s: %s", fact_id, exc)
            continue
        if not links:
            continue
        counts["links_created"] += len(links)

        # Enrichment is derived from (fact text, matched nodes) and is stored
        # beside the link in code_graph.db. The user's own fact wording in
        # memory.db is never rewritten: doing that would invalidate the fact's
        # embedding, and would compound a suffix on every maintenance cycle.
        try:
            matched = resolver.get_matched_nodes(content)
            if not matched:
                continue
            enriched = enricher.enrich(fact_id, matched, content)
            if enriched and enriched != content:
                _store_enrichment(code_graph_db, fact_id, enriched)
                counts["enriched"] += 1
        except Exception as exc:
            logger.debug("bridge enrichment failed for %s: %s", fact_id, exc)

    if newest and newest != watermark:
        try:
            code_graph_db.set_metadata(_WATERMARK_KEY, newest)
        except Exception as exc:
            logger.warning("bridge watermark not advanced: %s", exc)

    # One summary line per pass, never one per fact. A 3,527-fact store must not
    # produce 3,527 log lines; per-fact detail stays at debug.
    if counts["links_created"]:
        logger.info(
            "Code bridge: %d facts scanned, %d links, %d enriched",
            counts["facts_scanned"], counts["links_created"], counts["enriched"],
        )
    return counts


def _store_enrichment(
    code_graph_db: CodeGraphDatabase, fact_id: str, enriched: str
) -> None:
    """Persist enrichment text onto every link for *fact_id*."""
    code_graph_db.execute_write(
        "UPDATE code_memory_links SET enriched_description = ? WHERE slm_fact_id = ?",
        (enriched, fact_id),
    )
