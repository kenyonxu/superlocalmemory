# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Temporal Retrieval Channel (3-Date Model).

Searches by referenced_date (NOT just created_at like V1).
Returns empty when query has no temporal signal (no recency noise).

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from dateutil.parser import ParserError, parse as dateutil_parse

from superlocalmemory.encoding.temporal_parser import TemporalParser
from superlocalmemory.storage.database import _scope_where

if TYPE_CHECKING:
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

_MAX_PROXIMITY_DAYS: float = 365.0


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to timezone-aware UTC (naive values are assumed UTC).

    Keeps proximity and interval comparisons from mixing naive and aware values.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    # v3.5.0 perf: stored dates are ISO-8601, so try the C-level
    # datetime.fromisoformat (~1µs) FIRST. dateutil.parser.parse is ~100x
    # slower (~100µs) and the temporal channel parses up to 4 dates per event
    # across thousands of events — that was ~2.6s of the recall. dateutil is
    # now only the fallback for non-ISO strings.
    try:
        return _as_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        pass
    try:
        return _as_utc(dateutil_parse(s))
    except (ParserError, ValueError, OverflowError, TypeError):
        return None


def _proximity_score(q: datetime, e: datetime) -> float:
    """Gaussian proximity: same day=1.0, 30d=0.61, 90d=0.11."""
    dist = abs((q - e).total_seconds()) / 86400.0
    if dist > _MAX_PROXIMITY_DAYS:
        return 0.0
    return math.exp(-(dist * dist) / (2.0 * 30.0 * 30.0))


class TemporalChannel:
    """Date-aware retrieval using the 3-date temporal model."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def search(
        self,
        query: str,
        profile_id: str,
        top_k: int = 30,
        include_global: bool | None = None,
        include_shared: bool | None = None,
        query_type: str = "general",
    ) -> list[tuple[str, float]]:
        """Search for temporally relevant facts.

        Two strategies:
        1. Date proximity: scores events by date closeness to query date.
        2. Entity-temporal: filters events by entity name in query,
           returns ALL their temporal facts (metadata-first approach).

        Returns empty only when query has no temporal signal AND no
        entity-temporal matches.

        Args:
            include_global: Include global-scope facts. Falls back to the
                instance attribute when not supplied.
            include_shared: Include shared-scope facts. Same fallback.
        """
        if include_global is None:
            include_global = bool(getattr(self, "include_global", False))
        if include_shared is None:
            include_shared = bool(getattr(self, "include_shared", False))

        parser = TemporalParser()
        dates = parser.extract_dates_from_text(query)
        query_dt = _parse_iso(dates.get("referenced_date"))
        if query_dt is None:
            query_dt = self._try_parse(query)
        query_dt = _as_utc(query_dt)

        # Strategy 1: Entity-temporal metadata search
        # "When did Alice...?" → find all temporal events for Alice
        entity_results = self._entity_temporal_search(
            query, profile_id,
            include_global=include_global, include_shared=include_shared,
        )

        # Strategy 2: Date proximity search
        if query_dt is None:
            recent: list[tuple[str, float]] = []
            if query_type in ("recency", "temporal"):
                # A question about the present has no date to be near, so this
                # channel answers with what is actually recent.
                #
                # This deliberately does NOT depend on the entity search coming
                # back empty. It used to sit inside a guard that required both,
                # inherited from the case where there is simply nothing to do —
                # which meant a single entity match anywhere silently disabled
                # recency. On a real store something almost always matches, so
                # the fallback effectively never ran, and "what am I working on"
                # answered with month-old notes containing the word "working".
                recent = self._recency_fallback(
                    profile_id,
                    include_global=include_global,
                    include_shared=include_shared,
                )
            if not entity_results:
                return recent
            if recent:
                # Both signals are real: an entity the question named, and the
                # fact that the question is about now. Recency leads because
                # that is what this channel was asked about; entity matches
                # follow, and anything already present keeps its better place.
                seen = {fid for fid, _ in recent}
                return recent + [(f, s) for f, s in entity_results if f not in seen]

        events = self._load_events(
            profile_id, include_global=include_global, include_shared=include_shared,
        )
        scored: dict[str, float] = {}

        # Include entity-temporal results with high base score
        for fid, score in entity_results:
            scored[fid] = max(scored.get(fid, 0.0), score)

        if query_dt is not None:
            for ev in events:
                best = 0.0
                ref = _parse_iso(ev.get("referenced_date"))
                if ref is not None:
                    best = max(best, _proximity_score(query_dt, ref))

                obs = _parse_iso(ev.get("observation_date"))
                if obs is not None:
                    best = max(best, _proximity_score(query_dt, obs) * 0.8)

                i_start = _parse_iso(ev.get("interval_start"))
                i_end = _parse_iso(ev.get("interval_end"))
                if i_start and i_end:
                    if i_start <= query_dt <= i_end:
                        best = max(best, 1.0)
                    else:
                        best = max(best, max(
                            _proximity_score(query_dt, i_start),
                            _proximity_score(query_dt, i_end),
                        ) * 0.9)

                if best > 0.0:
                    fid = ev["fact_id"]
                    scored[fid] = max(scored.get(fid, 0.0), best)

        results = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _entity_temporal_search(
        self,
        query: str,
        profile_id: str,
        include_global: bool | None = None,
        include_shared: bool | None = None,
    ) -> list[tuple[str, float]]:
        """Metadata-first: find temporal events for entities mentioned in query.

        "When did Alice do X?" → SQL filter by entity_id for Alice → return
        all temporal facts about Alice. High precision for entity+time queries.
        """
        if include_global is None:
            include_global = bool(getattr(self, "include_global", False))
        if include_shared is None:
            include_shared = bool(getattr(self, "include_shared", False))
        import re
        _PROPER_RE = re.compile(r"\b([A-Z][a-z]+)\b")
        names = [m.group(1) for m in _PROPER_RE.finditer(query)]
        # Also try title-cased version for lowercase queries
        if not names:
            names = [m.group(1) for m in _PROPER_RE.finditer(query.title())]
        # Filter out common words from title-casing
        _stop = {"What", "When", "Where", "Who", "Which", "How", "Does", "Did",
                 "The", "That", "This", "There", "Then", "Have", "Has", "Had",
                 "About", "After", "Before", "From", "With", "Would", "Could",
                 "Should", "Will", "Because", "Also", "Just", "Like", "Know",
                 "Think", "Tell", "Said"}
        names = [n for n in names if n not in _stop]
        if not names:
            return []

        results: list[tuple[str, float]] = []
        seen: set[str] = set()
        where, params = _scope_where(
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
            prefix="af",
        )

        for name in names[:3]:  # Limit to first 3 entity mentions
            # Resolve the entity and event in one scope-filtered query. Looking
            # up the entity only in the requester's profile made global events
            # owned by another profile undiscoverable before authorization was
            # even evaluated.
            rows = self._db.execute(
                "SELECT te.fact_id FROM temporal_events AS te "
                "JOIN canonical_entities AS ce ON ce.entity_id = te.entity_id "
                "JOIN atomic_facts AS af ON af.fact_id = te.fact_id "
                f"WHERE {where} AND LOWER(ce.canonical_name) = LOWER(?)",
                (*params, name),
            )
            for row in rows:
                fid = dict(row)["fact_id"]
                if fid not in seen:
                    seen.add(fid)
                    # Rank by position (first events more likely relevant) instead
                    # of flat 0.85 which loses discrimination
                    rank_score = 0.85 - len(seen) * 0.02
                    results.append((fid, max(0.3, rank_score)))

        return results

    def _load_events(
        self,
        profile_id: str,
        include_global: bool | None = None,
        include_shared: bool | None = None,
    ) -> list[dict]:
        if include_global is None:
            include_global = bool(getattr(self, "include_global", False))
        if include_shared is None:
            include_shared = bool(getattr(self, "include_shared", False))
        where, params = _scope_where(
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
            prefix="af",
        )
        rows = self._db.execute(
            "SELECT te.fact_id, te.observation_date, te.referenced_date, "
            "te.interval_start, te.interval_end, af.created_at "
            "FROM temporal_events AS te "
            "JOIN atomic_facts AS af ON af.fact_id = te.fact_id "
            f"WHERE {where} "
            "ORDER BY te.rowid DESC LIMIT 5000",
            (*params,),
        )
        return [dict(r) for r in rows]

    def _recency_fallback(
        self,
        profile_id: str,
        include_global: bool | None,
        include_shared: bool | None,
    ) -> list[tuple[str, float]]:
        """Return recently created facts with Gaussian age-decay scoring.

        Called when the query carries no date and the caller has said the
        question is about the present. It deliberately does not depend on the
        entity search being empty — requiring that made this unreachable on any
        store where something matches, which is most of them.

        One entry per fact. Facts scored here compete in fusion against semantic
        and BM25 results, and fusion ranks facts, so repeating a fact spends
        ranks without adding candidates.

        Scoring: Gaussian with sigma=7 days. Facts older than 90 days score
        below 0.01 and are excluded. Returns at most 50 (fact_id, score) pairs,
        ordered highest-score first.
        """
        _SIGMA = 7.0          # days — tighter than _proximity_score's 30d
        _MAX_AGE_DAYS = 90.0  # cut-off: exp(-(90^2)/(2*7^2)) ≈ 0.0
        now_dt = datetime.now(tz=timezone.utc)

        events = self._load_events(
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
        )
        # Keyed by fact, not by event. A fact carries one row per temporal event
        # it participates in, so appending per event made a fact with six events
        # occupy six of the fifty slots. Measured on a real store: fifty entries
        # covering EIGHT distinct facts. Fusion ranks facts, so the channel
        # looked full while offering almost nothing, and genuinely recent facts
        # were crowded out by copies of each other.
        best: dict[str, float] = {}
        for ev in events:
            fid = ev.get("fact_id")
            if not fid:
                continue
            created = _parse_iso(ev.get("created_at"))
            if created is None:
                continue
            utc_created = _as_utc(created)
            if utc_created is None:
                continue
            age_days = max(
                0.0,
                (now_dt - utc_created).total_seconds() / 86400.0,
            )
            if age_days > _MAX_AGE_DAYS:
                continue
            score = math.exp(-(age_days ** 2) / (2.0 * _SIGMA * _SIGMA))
            if score > 0.01 and score > best.get(fid, 0.0):
                best[fid] = score

        out = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return out[:50]

    @staticmethod
    def _try_parse(text: str) -> datetime | None:
        """Fuzzy date parse with safety guards.

        dateutil fuzzy=True is exponential on long non-date text.
        Guard: only attempt on short strings (< 60 chars) that contain
        at least one digit (dates always have numbers).
        """
        if len(text) > 60 or not any(c.isdigit() for c in text):
            return None
        try:
            return dateutil_parse(text, fuzzy=True)
        except (ParserError, ValueError, OverflowError):
            return None
