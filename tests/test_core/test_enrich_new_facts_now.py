# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A memory should be findable by meaning as soon as it is stored.

Storing a memory and being able to find it again are different things. A fact
with no vector can only be matched on its own wording, which is not how anyone
asks a question — "what am I working on" shares no distinctive word with any
particular memory. Until the vector exists, the memory describing what is
happening right now is the hardest one in the store to find.

Every entry point — the command line, the tool interface and the dashboard —
commits through one durable receipt and then relies on a background pass to
attach the vector. These tests cover the bounded attempt made before that
receipt is returned, so the window is closed for all three at once rather than
for whichever door happened to be used.

The durable write has already happened by then. Nothing here can fail it: on any
error, any timeout, or a cold embedder, the fact simply keeps its place in the
background queue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from superlocalmemory.core.config import EmbeddingConfig, SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.storage.models import Mode


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    eng = MemoryEngine(
        SLMConfig(
            mode=Mode.A,
            base_dir=tmp_path,
            db_path=tmp_path / "memory.db",
            active_profile="default",
            embedding=EmbeddingConfig(model_name="nomic-embed-text", dimension=768),
        )
    )
    eng._ensure_init()
    return eng


def _fact_without_a_vector(engine: MemoryEngine, content: str) -> str:
    """Write a fact and strip every trace of its enrichment.

    This is the state a fact is in between "stored durably" and "the background
    pass has caught up", which is the window under test.
    """
    fact_id = engine.store_fast(content)[0]
    conn = sqlite3.connect(str(engine._config.db_path))
    try:
        conn.execute("DELETE FROM embedding_metadata WHERE fact_id = ?", (fact_id,))
        conn.execute("UPDATE atomic_facts SET embedding = NULL WHERE fact_id = ?", (fact_id,))
        conn.commit()
    finally:
        conn.close()
    return fact_id


def _state(engine: MemoryEngine, fact_id: str) -> tuple[str, int]:
    conn = sqlite3.connect(f"file:{engine._config.db_path}?mode=ro", uri=True)
    try:
        kind = conn.execute(
            "SELECT typeof(embedding) FROM atomic_facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        projected = conn.execute(
            "SELECT COUNT(*) FROM embedding_metadata WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        return kind, projected
    finally:
        conn.close()


class TestTheVectorIsAttached:
    def test_both_representations_are_written(self, engine: MemoryEngine) -> None:
        """The column alone is not enough, and this is the trap worth pinning.

        Searching by meaning reads the vector projection, not the canonical
        column. Writing only the column looks like success at the database level
        and leaves the memory exactly as unfindable as before.
        """
        fact_id = _fact_without_a_vector(engine, "The Helsinki ledger reconciliation is deferred.")
        assert _state(engine, fact_id) == ("null", 0), "test setup did not strip the vector"

        assert engine.enrich_new_facts_now([fact_id]) == 1

        kind, projected = _state(engine, fact_id)
        assert kind == "blob", (
            f"the embedding was stored as {kind!r}; a text value here means a writer "
            f"bypassed the codec and reintroduced the previous format"
        )
        assert projected == 1, (
            "the canonical column was written but the vector projection was not, so "
            "searching by meaning still cannot reach this memory"
        )

    def test_a_fact_that_already_has_one_is_left_alone(self, engine: MemoryEngine) -> None:
        fact_id = engine.store_fast("Procurement confirmed the tariff schedule.")[0]
        assert engine.enrich_new_facts_now([fact_id]) == 0, (
            "a fact that already has a vector was re-embedded, paying for work "
            "that was already done"
        )

    def test_nothing_to_do_is_not_an_error(self, engine: MemoryEngine) -> None:
        assert engine.enrich_new_facts_now([]) == 0
        assert engine.enrich_new_facts_now(["does-not-exist"]) == 0


class TestItCannotHarmTheWrite:
    """The durable write is already committed; this runs on top of it."""

    def test_a_cold_embedder_defers_instead_of_failing(self, engine: MemoryEngine) -> None:
        fact_id = _fact_without_a_vector(engine, "A note recorded while the embedder is cold.")
        cold = MagicMock()
        cold._available = False
        engine._embedder = cold
        assert engine.enrich_new_facts_now([fact_id]) == 0
        assert _state(engine, fact_id) == ("null", 0)
        cold.embed.assert_not_called()

    def test_an_embedder_that_raises_does_not_propagate(self, engine: MemoryEngine) -> None:
        fact_id = _fact_without_a_vector(engine, "A note recorded while the embedder is broken.")
        broken = MagicMock()
        broken._available = True
        broken.embed.side_effect = RuntimeError("model not loaded")
        engine._embedder = broken
        assert engine.enrich_new_facts_now([fact_id]) == 0

    def test_the_deadline_is_honoured(self, engine: MemoryEngine) -> None:
        """Past the deadline the caller proceeds and the background pass finishes.

        This daemon serves many sessions, so a slow embedder must cost the caller
        a bounded wait and nothing more.
        """
        import time as _time

        fact_id = _fact_without_a_vector(engine, "A note recorded while the embedder is slow.")
        slow = MagicMock()
        slow._available = True
        slow.embed.side_effect = lambda _t: (_time.sleep(1.5), [0.1] * 768)[1]
        engine._embedder = slow
        started = _time.monotonic()
        assert engine.enrich_new_facts_now([fact_id], timeout_s=0.2) == 0
        assert _time.monotonic() - started < 1.2, "the deadline was not enforced"
