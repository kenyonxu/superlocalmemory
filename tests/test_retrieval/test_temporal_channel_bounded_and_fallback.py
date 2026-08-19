# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for temporal_channel bounded event scan and recency fallback.

Covers:
  - _load_events() SQL contains ORDER BY and LIMIT (bounded, deterministic)
  - _load_events() returns newest events first (highest rowid → first result)
  - recency fallback fires when query_type is "recency" or "temporal"
    and the query has no parseable date and no entity match
  - recency fallback is NOT triggered for query_type "general",
    so factual lookups are unaffected by this logic
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.retrieval.temporal_channel import TemporalChannel
from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import (
    AtomicFact,
    CanonicalEntity,
    MemoryRecord,
    TemporalEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILE = "p1"
_ENTITY_ID = "e-dummy"


def _make_db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "bounded-test.db")
    manager.initialize(real_schema)
    manager.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name, description) VALUES (?, ?, '')",
        (_PROFILE, _PROFILE),
    )
    # Canonical entity required by FK in temporal_events
    manager.execute(
        "INSERT OR IGNORE INTO canonical_entities "
        "(entity_id, profile_id, canonical_name, entity_type, first_seen, last_seen, fact_count) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), 0)",
        (_ENTITY_ID, _PROFILE, "Dummy", "concept"),
    )
    return manager


def _seed_fact_and_event(
    db: DatabaseManager,
    fact_id: str,
    referenced_date: str = "2026-01-01",
) -> None:
    """Insert a memory → fact → temporal_event chain for testing."""
    mem = MemoryRecord(
        memory_id=f"m-{fact_id}",
        profile_id=_PROFILE,
        scope="personal",
        content=fact_id,
    )
    db.store_memory(mem)
    db.store_fact(AtomicFact(
        fact_id=fact_id,
        memory_id=mem.memory_id,
        profile_id=_PROFILE,
        scope="personal",
        content=f"fact content for {fact_id}",
    ))
    db.store_temporal_event(TemporalEvent(
        event_id=f"t-{fact_id}",
        profile_id=_PROFILE,
        entity_id=_ENTITY_ID,
        fact_id=fact_id,
        referenced_date=referenced_date,
        description=fact_id,
        scope="personal",
    ))


# ---------------------------------------------------------------------------
# 1.6 — Bounded event scan
# ---------------------------------------------------------------------------

class TestBoundedEventScan:
    """_load_events() must be bounded and deterministically ordered."""

    def test_a_dated_question_is_bounded_around_that_date(self) -> None:
        """Bounded is not enough — it must be bounded around what was ASKED for.

        The bound added for scan cost took the newest rows by insertion order.
        That is right for "what is recent" and wrong for "what happened in March
        last year": those events carry old row ids, were never loaded, and the
        answer came back empty rather than slow. The existing test above requires
        a LIMIT, so it passed the whole time this was broken — a bound can be
        present and still be the wrong bound.
        """
        db = MagicMock()
        db.execute.return_value = []
        ch = TemporalChannel(db)
        ch._load_events(_PROFILE, near_date="2024-03-15")
        sql, params = db.execute.call_args[0][0], db.execute.call_args[0][1]
        assert "julianday" in sql, (
            "a dated question was bounded by insertion order, so events near the "
            "date asked about are excluded whenever the store has newer ones"
        )
        assert "2024-03-15" in params, "the date asked about never reached the query"
        assert "LIMIT" in sql.upper(), "the scan must still be bounded"

    def test_sql_contains_limit_and_order_by(self) -> None:
        """The SQL issued by _load_events() must contain both ORDER BY and LIMIT.

        Without ORDER BY, LIMIT produces a non-deterministic subset.
        Without LIMIT, the full table is scanned regardless of size.
        """
        db = MagicMock()
        db.execute.return_value = []
        ch = TemporalChannel(db)
        ch._load_events(_PROFILE)
        issued_sql: str = db.execute.call_args[0][0]
        assert "LIMIT" in issued_sql, (
            f"_load_events SQL has no LIMIT — unbounded scan: {issued_sql!r}"
        )
        assert "ORDER BY" in issued_sql, (
            f"_load_events SQL has no ORDER BY — LIMIT would be non-deterministic: {issued_sql!r}"
        )

    def test_newest_event_returned_first(self, tmp_path: Path) -> None:
        """Events are returned newest-first (highest rowid first).

        Insertion order: a-first → b-second → c-third.
        Without ORDER BY te.rowid DESC the table scan returns ascending rowid
        (a-first first). With ORDER BY DESC, c-third appears first.
        """
        db = _make_db(tmp_path)
        for fid in ("a-first", "b-second", "c-third"):
            _seed_fact_and_event(db, fid)

        ch = TemporalChannel(db)
        rows = ch._load_events(_PROFILE)
        assert len(rows) == 3, f"expected 3 events, got {len(rows)}"
        assert rows[0]["fact_id"] == "c-third", (
            f"expected newest event (c-third) first, got {rows[0]['fact_id']!r}. "
            "ORDER BY te.rowid DESC is missing."
        )

    def test_repeated_calls_return_identical_ordering(self, tmp_path: Path) -> None:
        """Two successive calls with the same DB state must return the same row sequence.

        This verifies determinism at the application level. It holds only when
        ORDER BY is present; without it the result set is implementation-defined.
        """
        db = _make_db(tmp_path)
        for fid in ("x1", "x2", "x3"):
            _seed_fact_and_event(db, fid)

        ch = TemporalChannel(db)
        first = [r["fact_id"] for r in ch._load_events(_PROFILE)]
        second = [r["fact_id"] for r in ch._load_events(_PROFILE)]
        assert first == second, (
            f"_load_events returned different orderings on successive calls: "
            f"{first} vs {second}"
        )

    def test_created_at_included_in_event_dict(self, tmp_path: Path) -> None:
        """_load_events() events must include created_at from atomic_facts.

        This field is used by the recency fallback to compute age-decay scores.
        If absent, the fallback silently returns nothing.
        """
        db = _make_db(tmp_path)
        _seed_fact_and_event(db, "has-created-at")

        ch = TemporalChannel(db)
        rows = ch._load_events(_PROFILE)
        assert rows, "_load_events returned no rows"
        assert "created_at" in rows[0], (
            f"created_at missing from _load_events result dict. "
            f"Keys present: {list(rows[0].keys())}"
        )


# ---------------------------------------------------------------------------
# 2.4 — Recency fallback: fires only for time-query types
# ---------------------------------------------------------------------------

class TestRecencyFallback:
    """Recency fallback is gated on query_type; factual lookups are unaffected."""

    @pytest.fixture()
    def db_with_recent_facts(self, tmp_path: Path) -> DatabaseManager:
        """DB seeded with 3 facts created just now (will have recent created_at)."""
        db = _make_db(tmp_path)
        for fid in ("recent-1", "recent-2", "recent-3"):
            _seed_fact_and_event(db, fid)
        return db

    def test_recency_query_type_returns_recent_facts(
        self, db_with_recent_facts: DatabaseManager,
    ) -> None:
        """When query_type='recency' and no date/entity, the fallback must fire.

        Before the change: search() has no query_type parameter → TypeError.
        After the change: returns a non-empty list of recently created facts.
        """
        ch = TemporalChannel(db_with_recent_facts)
        with patch(
            "superlocalmemory.retrieval.temporal_channel.TemporalParser",
        ) as MockParser:
            MockParser.return_value.extract_dates_from_text.return_value = {
                "referenced_date": None,
            }
            results = ch.search(
                "what am I working on",
                _PROFILE,
                query_type="recency",
            )
        assert len(results) > 0, (
            "Recency fallback returned nothing for query_type='recency'. "
            "Expected recently created facts."
        )
        # All returned scores must be in (0.0, 1.0] — valid Gaussian decay values
        for fid, score in results:
            assert 0.0 < score <= 1.0, (
                f"Fallback score {score!r} for {fid!r} is outside (0, 1]"
            )

    def test_temporal_query_type_returns_recent_facts(
        self, db_with_recent_facts: DatabaseManager,
    ) -> None:
        """query_type='temporal' with no parseable date also uses the fallback."""
        ch = TemporalChannel(db_with_recent_facts)
        with patch(
            "superlocalmemory.retrieval.temporal_channel.TemporalParser",
        ) as MockParser:
            MockParser.return_value.extract_dates_from_text.return_value = {
                "referenced_date": None,
            }
            results = ch.search(
                "lately what has been happening",
                _PROFILE,
                query_type="temporal",
            )
        assert len(results) > 0, (
            "Recency fallback returned nothing for query_type='temporal'."
        )

    def test_general_query_type_does_not_trigger_fallback(
        self, db_with_recent_facts: DatabaseManager,
    ) -> None:
        """query_type='general' (the default) must NOT trigger the recency fallback.

        A factual question like 'what is Python' has no date or entity and
        should return [] regardless of recent facts in the DB.
        Before the change: TypeError on query_type= kwarg.
        After the change: returns [] because query_type='general' exits early.
        """
        ch = TemporalChannel(db_with_recent_facts)
        with patch(
            "superlocalmemory.retrieval.temporal_channel.TemporalParser",
        ) as MockParser:
            MockParser.return_value.extract_dates_from_text.return_value = {
                "referenced_date": None,
            }
            results = ch.search(
                "what is Python",
                _PROFILE,
                query_type="general",
            )
        assert results == [], (
            f"Factual query with query_type='general' must return [] "
            f"even when recent facts exist. Got: {results!r}"
        )

    def test_default_query_type_preserves_existing_empty_return(
        self, db_with_recent_facts: DatabaseManager,
    ) -> None:
        """Calling search() WITHOUT query_type must still return [] for no-signal queries.

        This guards backward compatibility: all existing callers that do not
        pass query_type must get the old behaviour unchanged.
        """
        ch = TemporalChannel(db_with_recent_facts)
        with patch(
            "superlocalmemory.retrieval.temporal_channel.TemporalParser",
        ) as MockParser:
            MockParser.return_value.extract_dates_from_text.return_value = {
                "referenced_date": None,
            }
            results = ch.search("what is Python", _PROFILE)
        assert results == [], (
            f"search() without query_type must return [] for no-signal query. "
            f"Got: {results!r}"
        )

    def test_most_recent_fact_scores_highest(
        self, tmp_path: Path,
    ) -> None:
        """The most recently created fact must receive the highest fallback score.

        Facts are scored by Gaussian age-decay (sigma=7d). A fact created
        seconds ago scores near 1.0 and must rank above older facts.
        """
        db = _make_db(tmp_path)
        # Insert 3 facts; all will have created_at ≈ now (within test runtime)
        for fid in ("q1", "q2", "q3"):
            _seed_fact_and_event(db, fid)

        ch = TemporalChannel(db)
        with patch(
            "superlocalmemory.retrieval.temporal_channel.TemporalParser",
        ) as MockParser:
            MockParser.return_value.extract_dates_from_text.return_value = {
                "referenced_date": None,
            }
            results = ch.search("what am I working on", _PROFILE, query_type="recency")

        assert results, "Fallback returned no results"
        top_score = results[0][1]
        # All facts were created within the last few seconds → scores near 1.0
        assert top_score > 0.9, (
            f"Most recent fact should score >0.9, got {top_score:.4f}. "
            "Gaussian sigma=7d means a few-second-old fact is essentially score=1.0."
        )


class TestOneEntryPerFactNotPerEvent:
    """The channel offers facts, so a fact must occupy exactly one slot.

    A fact carries one temporal_events row per event it participates in. The
    fallback used to append per event, so a fact with six events took six of the
    fifty slots it returns. Measured on a real store, fifty entries covered
    EIGHT distinct facts — the channel looked full while offering almost
    nothing, and genuinely recent facts were crowded out by copies of one
    another. Fusion ranks facts; spending ranks on duplicates buys nothing.
    """

    def test_a_fact_with_many_events_appears_once(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        # three facts, each carrying several events, exactly as a real store does
        for fid in ("multi-1", "multi-2", "multi-3"):
            _seed_fact_and_event(db, fid)
            for n, extra in enumerate(("2026-02-02", "2026-03-03", "2026-04-04")):
                db.store_temporal_event(TemporalEvent(
                    event_id=f"t-{fid}-{n}", profile_id=_PROFILE,
                    entity_id=_ENTITY_ID, fact_id=fid,
                    referenced_date=extra, description=fid, scope="personal",
                ))
        ch = TemporalChannel(db)
        out = ch._recency_fallback(_PROFILE, include_global=False, include_shared=False)
        ids = [fid for fid, _ in out]
        assert ids, "the fallback returned nothing for recently created facts"
        assert len(ids) == len(set(ids)), (
            f"the fallback returned {len(ids)} entries covering only "
            f"{len(set(ids))} facts; a fact with several events is spending "
            f"several of the slots this channel has to offer"
        )

    def test_a_fact_keeps_its_best_score(self, tmp_path: Path) -> None:
        """Collapsing duplicates must not silently pick the worst of them."""
        db = _make_db(tmp_path)
        _seed_fact_and_event(db, "scored-1")
        db.store_temporal_event(TemporalEvent(
            event_id="t-scored-1-b", profile_id=_PROFILE, entity_id=_ENTITY_ID,
            fact_id="scored-1", referenced_date="2026-05-05",
            description="scored-1", scope="personal",
        ))
        ch = TemporalChannel(db)
        out = dict(ch._recency_fallback(_PROFILE, include_global=False, include_shared=False))
        assert out, "expected the recently created fact to be returned"
        # created moments ago, so the age-decay score is essentially 1.0
        assert out["scored-1"] > 0.9, (
            f"a fact created just now scored {out['scored-1']:.4f}; collapsing its "
            f"duplicate events must keep the best score, not the last one seen"
        )
