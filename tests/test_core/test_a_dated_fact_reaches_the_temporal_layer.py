# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A date is a date whether or not an entity was recognised.

The temporal-event write used to be gated on ``fact.canonical_entities and
has_dates``. So a memory carrying a perfectly good date but no recognised
entity got no temporal row at all, and the temporal channel could only reach it
through its created_at recency fallback -- by being recent, rather than by being
about the right time.

Measured on the author's store before this change:

    genuine facts                                    3,894
      ...with no canonical entity                      967   (24.8%)
      ...with no canonical entity AND no temporal row  958

A quarter of the store, absent from the layer whose whole job is dates.

``temporal_events.entity_id`` is NOT NULL with a foreign key to
``canonical_entities``, so an entity-less event needs a row to point at. It gets
a per-profile placeholder that is deliberately inert -- the tests below pin that
inertness, because a placeholder that leaked into entity ranking would recreate
the saturation problem it was written to avoid ('State' links 1,388 facts on the
same store).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from superlocalmemory.core.config import Mode, RetrievalConfig, SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.store_pipeline import _UNRESOLVED_ENTITY_PREFIX


class _MockEmbedder:
    """Deterministic mock embedder: text -> 768-dim vector via hashing.

    Shares its contract with tests/test_final_locomo_mini.py. A first draft here
    omitted ``is_available`` and ``compute_fisher_params``; engine wiring then
    fell back to "BM25-only mode" and materialization failed with "incomplete
    derivation stages: ann, vector" -- so every assertion below was about a store
    that had never finished being written.
    """

    is_available = True

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        rng = np.random.default_rng(int.from_bytes(h[:8], "little"))
        vec = rng.standard_normal(self.dimension).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def compute_fisher_params(
        self, embedding: list[float],
    ) -> tuple[list[float], list[float]]:
        arr = np.asarray(embedding, dtype=np.float64)
        norm = float(np.linalg.norm(arr))
        if norm < 1e-10:
            mean = np.zeros(len(arr))
            var = np.full(len(arr), 2.0)
        else:
            mean = arr / norm
            abs_mean = np.abs(mean)
            max_val = float(np.max(abs_mean)) + 1e-10
            var = np.clip(2.0 - 1.7 * (abs_mean / max_val), 0.3, 2.0)
        return mean.tolist(), var.tolist()


@pytest.fixture()
def engine(tmp_path: Path) -> MemoryEngine:
    """Mode A engine with SYNCHRONOUS enrichment.

    Enrichment is deferred to the daemon materializer by default, so a plain
    engine.store() commits the queryable row and returns before the temporal
    layer runs. Asserting on temporal_events without forcing sync enrichment
    measures nothing -- an early draft of this file did exactly that and read
    zero events on code that works.
    """
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.db_path = tmp_path / "memory.db"
    config.retrieval = RetrievalConfig(use_cross_encoder=False, agentic_max_rounds=0)
    eng = MemoryEngine(config)
    with patch(
        "superlocalmemory.core.embeddings.EmbeddingService",
        return_value=_MockEmbedder(768),
    ):
        eng.initialize()
    from tests.conftest import force_sync_enrichment

    return force_sync_enrichment(eng)


def _rows(db: Path, sql: str, *params: object) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


#: Deliberately entity-free prose. Nothing here resolves to a canonical entity,
#: which is the case the old gate silently dropped.
_NO_ENTITY = "The throughput measurement finished without incident."


class TestADatedFactGetsATemporalRow:
    def test_a_fact_with_no_entity_still_gets_one(self, engine) -> None:
        db = Path(engine._config.db_path)
        engine.store(content=_NO_ENTITY, session_date="2026-03-14")

        facts = _rows(db, "SELECT fact_id, canonical_entities_json, observation_date "
                          "FROM atomic_facts WHERE memory_id <> ''")
        assert facts, "nothing was stored"
        entity_free = [
            f for f in facts
            if f["canonical_entities_json"] in ("[]", "", None)
        ]
        assert entity_free, (
            "the fixture resolved an entity after all, so this test does not "
            f"exercise the gate: {facts}"
        )
        for fact in entity_free:
            assert fact["observation_date"], "no date, so nothing to assert"
            events = _rows(
                db, "SELECT entity_id, observation_date FROM temporal_events "
                    "WHERE fact_id = ?", fact["fact_id"],
            )
            assert events, (
                f"a dated fact with no entity got no temporal row: {fact}"
            )
            assert events[0]["observation_date"] == fact["observation_date"]

    def test_no_dated_fact_is_left_out(self, engine) -> None:
        """The property, over a mixed batch, rather than one hand-picked case."""
        db = Path(engine._config.db_path)
        for content in (
            _NO_ENTITY,
            "Varun approved the release on a Tuesday.",
            "The queue drained overnight and stayed empty.",
            "Qualixar published the reliability report.",
        ):
            engine.store(content=content, session_date="2026-04-02")

        missing = _rows(db, """
            SELECT af.fact_id, af.canonical_entities_json, substr(af.content,1,60) c
              FROM atomic_facts af
              LEFT JOIN temporal_events te ON te.fact_id = af.fact_id
             WHERE af.memory_id <> ''
               AND te.fact_id IS NULL
               AND (af.observation_date IS NOT NULL
                    OR af.referenced_date IS NOT NULL
                    OR af.interval_start IS NOT NULL)
        """)
        assert missing == [], (
            f"{len(missing)} dated facts have no temporal row: "
            f"{[m['c'] for m in missing]}"
        )

    def test_an_undated_fact_gets_no_event_and_no_placeholder(
        self, engine,
    ) -> None:
        """The negative control.

        Without it, "every dated fact has an event" would also be satisfied by
        writing an event for everything, which would fill the temporal channel
        with rows carrying no date to rank on.
        """
        db = Path(engine._config.db_path)
        engine.store(content=_NO_ENTITY, session_date="")

        undated = _rows(db, "SELECT fact_id FROM atomic_facts "
                            "WHERE memory_id <> '' AND observation_date IS NULL "
                            "  AND referenced_date IS NULL "
                            "  AND interval_start IS NULL")
        for fact in undated:
            assert _rows(
                db, "SELECT 1 FROM temporal_events WHERE fact_id = ?",
                fact["fact_id"],
            ) == [], "an undated fact got a temporal event with nothing to rank on"


class TestThePlaceholderCannotDistortRanking:
    """A placeholder that leaked into ranking would be worse than the gap.

    On the same store, 'State' links 1,388 facts (27%) and flattens entity
    proximity to noise. One entity attached to a quarter of the corpus is
    exactly that failure, so these tests pin that the placeholder is invisible
    to everything except the foreign key it exists to satisfy.
    """

    def test_it_is_never_added_to_a_facts_own_entity_list(self, engine) -> None:
        """The entity channel reads canonical_entities_json, not temporal_events.

        As long as the placeholder stays out of that column it cannot become a
        ranking signal, however many facts point at it.
        """
        db = Path(engine._config.db_path)
        for i in range(4):
            engine.store(content=f"{_NO_ENTITY} Run {i}.", session_date="2026-05-05")

        polluted = _rows(
            db,
            "SELECT fact_id FROM atomic_facts "
            "WHERE canonical_entities_json LIKE ?",
            f"%{_UNRESOLVED_ENTITY_PREFIX}%",
        )
        assert polluted == [], (
            "the placeholder reached a fact's canonical entity list, where the "
            "entity channel would start ranking on it"
        )

    def test_it_is_one_row_per_profile_and_carries_no_name(self, engine) -> None:
        db = Path(engine._config.db_path)
        for i in range(3):
            engine.store(content=f"{_NO_ENTITY} Pass {i}.", session_date="2026-05-05")

        rows = _rows(
            db,
            "SELECT entity_id, canonical_name, entity_type, fact_count "
            "FROM canonical_entities WHERE entity_id LIKE ?",
            f"{_UNRESOLVED_ENTITY_PREFIX}%",
        )
        assert len(rows) == 1, f"expected one placeholder per profile, got {rows}"
        assert rows[0]["canonical_name"] == "", (
            "a named placeholder can be matched by an entity lookup"
        )
        assert rows[0]["entity_type"] == "unresolved"
        assert rows[0]["fact_count"] == 0, (
            "fact_count must stay 0 -- it is a foreign-key hook, not a concept"
        )

    def test_the_entity_name_lookup_cannot_match_it(self) -> None:
        """Its name is empty, so an empty query name would match it.

        Entity extraction does not produce empty names, so this is belt and
        braces -- but it is one line and it closes the only route by which the
        placeholder could surface as a real entity hit.
        """
        import inspect

        from superlocalmemory.retrieval import temporal_channel

        src = inspect.getsource(temporal_channel)
        assert "if not name or not name.strip():" in src, (
            "the entity-name lookup no longer skips empty names, so the "
            "placeholder entity can be matched by one"
        )

    def test_nothing_derives_entity_links_from_temporal_events(self) -> None:
        """Why attaching events to a placeholder is safe at all.

        If a retrieval module started reading temporal_events.entity_id to build
        entity associations, the placeholder would instantly become the most
        connected entity in the store.
        """
        import pathlib

        from superlocalmemory import retrieval

        root = pathlib.Path(retrieval.__file__).parent
        offenders = []
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "temporal_events" not in text:
                continue
            if path.name == "temporal_channel.py":
                continue  # the one legitimate reader, and it joins by name
            offenders.append(path.name)
        assert offenders == [], (
            f"modules other than temporal_channel now read temporal_events: "
            f"{offenders}; check none of them derive entity links from it"
        )
