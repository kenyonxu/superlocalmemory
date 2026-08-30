# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com

"""Progressive-abstraction read API (Wave Q3).

Exposes the abstraction hierarchy so the dashboard can browse it and drill
down to source atoms:

  GET /api/v3/abstraction/persona      — the per-profile persona roll-up
  GET /api/v3/abstraction/communities  — community summaries (Q2)
  GET /api/v3/abstraction/consolidated — display-only cluster summaries
  GET /api/v3/abstraction/health       — can my memories be found? (4.0.10)
  GET /api/v3/abstraction/sources      — drill-down (node -> source atoms)

Read-only, profile-scoped (Rule 01), direct sqlite3 (Rule 06). All handlers
fail-soft: a missing DB or table returns an empty payload, never a 500.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from superlocalmemory.server.routes.helpers import DB_PATH, get_active_profile, get_read_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/abstraction", tags=["abstraction"])

#: How many summary rows /consolidated will read before ranking them by
#: quality. Bounded because this runs on a request thread: a store with tens of
#: thousands of summaries must not turn one card into a full-table scan.
_SCAN_CEILING = 400

#: Characters of normalised opening text that make two summaries "the same
#: summary" for display. Long enough that two genuinely different subjects
#: diverge within it, short enough to catch the same sentence with a different
#: tail — which is the shape the summarizer actually produces.
_OPENING_KEY_CHARS = 90


def _opening_key(content: object) -> str:
    """Normalised opening of a summary, for near-duplicate collapsing.

    Case-folded with runs of whitespace flattened, so two summaries differing
    only in line wrapping or capitalisation collapse together. Returns "" for
    anything too short to judge, which is then never collapsed — better to show
    a duplicate than to hide a distinct summary on a weak signal.
    """
    text = " ".join(str(content or "").split()).casefold()
    if len(text) < 40:
        return ""
    return text[:_OPENING_KEY_CHARS]


class _ReadDB:
    """Adapt a raw sqlite3 connection to the .execute(...) -> list contract
    the read-only builder methods expect (matches DatabaseManager.execute)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple = ()) -> list:
        return self._conn.execute(sql, params).fetchall()


def _conn() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    return get_read_connection(DB_PATH)


@router.get("/persona")
def get_persona(profile: str = Query("")) -> JSONResponse:
    pid = profile or get_active_profile()
    conn = _conn()
    if conn is None:
        return JSONResponse({"profile": pid, "persona": None})
    try:
        from superlocalmemory.core.progressive_abstraction import ProgressiveAbstraction

        persona = ProgressiveAbstraction(_ReadDB(conn)).get_persona(pid)
        return JSONResponse({"profile": pid, "persona": persona})
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("persona read failed: %s", exc)
        return JSONResponse({"profile": pid, "persona": None})
    finally:
        conn.close()


@router.get("/communities")
def get_communities(profile: str = Query("")) -> JSONResponse:
    pid = profile or get_active_profile()
    conn = _conn()
    if conn is None:
        return JSONResponse({"profile": pid, "communities": []})
    try:
        from superlocalmemory.core.community_summary import CommunitySummaryBuilder

        summaries = CommunitySummaryBuilder(_ReadDB(conn)).get_summaries(pid)
        return JSONResponse({"profile": pid, "communities": summaries})
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("communities read failed: %s", exc)
        return JSONResponse({"profile": pid, "communities": []})
    finally:
        conn.close()


@router.get("/sources")
def get_sources(
    profile: str = Query(""), node: str = Query("persona"),
) -> JSONResponse:
    pid = profile or get_active_profile()
    conn = _conn()
    empty: dict[str, Any] = {
        "node_id": node, "node_type": "unknown", "communities": [], "fact_ids": [],
    }
    if conn is None:
        return JSONResponse({"profile": pid, "sources": empty})
    try:
        from superlocalmemory.core.progressive_abstraction import ProgressiveAbstraction

        node_val: Any = node
        if node != "persona":
            try:
                node_val = int(node)
            except (ValueError, TypeError):
                node_val = node
        sources = ProgressiveAbstraction(_ReadDB(conn)).get_sources(pid, node_val)
        return JSONResponse({"profile": pid, "sources": sources})
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("sources read failed: %s", exc)
        return JSONResponse({"profile": pid, "sources": empty})
    finally:
        conn.close()


@router.get("/consolidated")
def get_consolidated(
    profile: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    include_unusable: bool = Query(False),
) -> JSONResponse:
    """Cluster summaries, read from the DISPLAY table and nowhere else.

    ``consolidated_summaries`` is the only source. Reading ``atomic_facts``
    here would put the boundary back where it was: these summaries were in the
    retrieval corpus until 4.0.10 and the whole point of moving them is that
    exactly one surface shows them, and it is this one.

    ``summaries`` holds only rows worth reading. The rest are REPORTED, not
    returned: ``unusable`` and ``near_duplicates`` are counts over the scanned
    window. A reader is better served by "62 of these came back empty" than by a
    page that silently shows a handful and looks complete — and hiding the fact
    that they came back empty would hide the problem this endpoint exists to make
    visible. ``include_unusable=true`` returns them for inspection.

    Two orderings, both measured rather than chosen:

    * Ranking by ``source_count`` alone put the junk on top, because the
      summaries merging the largest clusters are exactly the ones the model had
      least in common to work with. On the author's store **0 of the top 24 by
      source_count were usable**, so a card asking for 24 rendered empty against
      a store holding a thousand summaries.
    * Rows covering real memories rank above rows covering none. 353 of these
      are summaries of summaries; their honest ``source_count`` is 0, and a
      digest of the summarizer's own output is worth less to a reader than a
      digest of their own words.

    Quality is a Python predicate rather than a SQL expression, which is why the
    window is read to at most ``_SCAN_CEILING`` rows, classified, and then
    ordered.
    """
    pid = profile or get_active_profile()
    conn = _conn()
    if conn is None:
        return JSONResponse({
            "profile": pid, "summaries": [], "unusable": 0, "scanned": 0,
        })
    try:
        from superlocalmemory.summaries.base import clean_llm_summary
        from superlocalmemory.summaries.non_answer import (
            MIN_USEFUL_CHARS,
            is_non_answer,
        )

        scan = min(_SCAN_CEILING, max(int(limit) * 8, int(limit)))
        rows = conn.execute(
            "SELECT summary_id, entity_name, content, source_count, "
            "       generated_by, source_earliest, source_latest, created_at "
            "  FROM consolidated_summaries "
            " WHERE profile_id = ? "
            " ORDER BY source_count DESC, created_at DESC, summary_id ASC "
            " LIMIT ?",
            (pid, scan),
        ).fetchall()

        classified: list[dict[str, Any]] = []
        unusable = 0
        for row in rows:
            item = dict(row)
            # CLEAN, THEN JUDGE — the same order the write path uses, and for
            # the same reason. Rows migrated from the old corpus were never
            # cleaned, so their scaffolding is still attached: judging first let
            # "Here is a concise summary paragraph incorporating all 10 facts..."
            # through as usable and put it at the top of the card, because it
            # does contain a summary and the non-answer rules are about refusals,
            # not preambles. Cleaning is also what the reader should see: the
            # scaffolding is addressed to a conversation they cannot read.
            item["content"] = clean_llm_summary(str(item.get("content") or ""))
            rejected, why = is_non_answer(
                item["content"], min_chars=MIN_USEFUL_CHARS,
            )
            item["quality"] = why if rejected else "ok"
            if rejected:
                unusable += 1
            classified.append(item)

        # Usable first, then rows that cover real memories, then the SQL
        # ordering within each group. Stable sort, so two runs of one request
        # return the same rows in the same order — a summary card that
        # reshuffles itself on refresh reads as a bug even when every row is
        # correct.
        classified.sort(key=lambda item: (
            0 if item["quality"] == "ok" else 1,
            0 if (item.get("source_count") or 0) > 0 else 1,
        ))

        # Collapse near-duplicates.
        #
        # The table's UNIQUE constraint is on exact content, so summaries that
        # differ by a clause survive as separate rows. On the author's store the
        # first 24 usable rows all opened "The Pro and SuperLocalMemory (SLM)
        # projects have made significant progress in..." — twenty-four cards
        # saying one thing, which reads as a broken page rather than as a view
        # of a memory.
        #
        # Collapsed on a normalised opening, keeping the row that merged the
        # most memories (the ordering above already put it first). The count is
        # reported, not swallowed: that these summaries repeat each other is a
        # real property of the store and worth a reader knowing.
        deduped: list[dict[str, Any]] = []
        seen_openings: set[str] = set()
        collapsed = 0
        for item in classified:
            key = _opening_key(item.get("content"))
            if key and key in seen_openings:
                collapsed += 1
                continue
            if key:
                seen_openings.add(key)
            deduped.append(item)

        # Only rows worth reading occupy the window.
        #
        # A first draft returned everything, usable first, and truncated at
        # `limit`. Because the usable rows on this store collapse to a handful
        # of distinct openings, the tail of a limit-10 request filled with
        # refusals — and a card asking for 10 got 2 it could render and 8 it
        # threw away. The counts carry what the reader needs to know about the
        # rest; the rows themselves add nothing to a card.
        shown = (
            deduped[:int(limit)] if include_unusable
            else [i for i in deduped if i["quality"] == "ok"][:int(limit)]
        )
        return JSONResponse({
            "profile": pid,
            "summaries": shown,
            "unusable": unusable,
            "near_duplicates": collapsed,
            "scanned": len(rows),
        })
    except sqlite3.Error as exc:
        # A store that predates the display table. Empty, not an error.
        logger.debug("consolidated summaries read failed: %s", exc)
        return JSONResponse({
            "profile": pid, "summaries": [], "unusable": 0, "scanned": 0,
        })
    finally:
        conn.close()


@router.get("/health")
def get_memory_health() -> JSONResponse:
    """Whether this store's memories can actually be found.

    Same measurement ``slm doctor`` prints, so the dashboard and the CLI cannot
    tell the owner two different things. Read-only and fail-soft.
    """
    try:
        from superlocalmemory.core.memory_health import describe, measure

        health = measure(DB_PATH)
        return JSONResponse({
            "live_facts": health.live_facts,
            "findable_by_meaning": health.findable_by_meaning,
            "missing_vector": health.missing_vector,
            "withheld_summaries": health.withheld_summaries,
            "display_summaries": health.display_summaries,
            "hidden_by_forgetting": health.hidden_by_forgetting,
            "inconsistently_hidden": health.inconsistently_hidden,
            "reachability": round(health.reachability, 4),
            "healthy": health.healthy,
            "unavailable": list(health.unavailable),
            "summary": describe(health),
        })
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("memory health read failed: %s", exc)
        return JSONResponse({"healthy": None, "summary": [], "unavailable": ["error"]})
