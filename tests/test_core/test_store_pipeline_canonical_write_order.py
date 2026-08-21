# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The canonical embedding column must be written AFTER the projection attempt.

When the queryable-promotion path in run_store calls db.update_fact with the
embedding field before _upsert_fact_vectors tries to project, a refused
projection leaves the fact in a permanently stuck state:

  * atomic_facts.embedding IS NOT NULL  — maintenance passes (WHERE embedding
    IS NULL) never find it.
  * No entry in fact_embeddings          — meaning-based search never returns it.

The correct order mirrors _attach_vector in engine.py: projection first,
canonical write second, and the canonical is written regardless of whether the
projection accepted the vector (the vector is real data; withholding it would
force every repair pass to pay for a model call that the same installation
condition would refuse again anyway).

For run_store_fact_direct the same rule applies to the inline vector write:
the vector store must be attempted before the ANN index, and ANN is only
updated when the projection succeeded.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from superlocalmemory.core.store_pipeline import _upsert_fact_vectors
from superlocalmemory.storage.models import AtomicFact, FactType


def _fact(fid: str = "f-promo", content: str = "promoted queryable fact",
          embedding: list[float] | None = None) -> AtomicFact:
    f = AtomicFact(
        fact_id=fid,
        content=content,
        fact_type=FactType.SEMANTIC,
        embedding=embedding or [0.1] * 8,
    )
    return f


# ---------------------------------------------------------------------------
# _upsert_fact_vectors — projection before ANN, canonical is caller's job
# ---------------------------------------------------------------------------


class TestUpsertFactVectorsWriteOrder:
    """The vector store must be attempted before the ANN index."""

    def test_vector_store_called_before_ann_on_success(self) -> None:
        """On a successful projection the call order must be VS then ANN."""
        fact = _fact()
        call_log: list[str] = []

        ann = MagicMock()
        ann.add.side_effect = lambda *a, **kw: call_log.append("ann")

        vs = MagicMock()
        vs.available = True
        vs.upsert.side_effect = lambda **kw: call_log.append("vs") or True

        _upsert_fact_vectors(fact, "default", ann, vs)

        assert call_log == ["vs", "ann"], (
            f"wrong call order: {call_log}. VS must be written before ANN."
        )

    def test_ann_never_called_when_vector_store_refuses(self) -> None:
        """VS refuses → projected=False → ANN must not be touched."""
        fact = _fact()
        ann = MagicMock()
        vs = MagicMock()
        vs.available = True
        vs.upsert.return_value = False

        _upsert_fact_vectors(fact, "default", ann, vs)

        ann.add.assert_not_called()

    def test_end_state_after_projection_failure_no_ann_entry(self) -> None:
        """After a projection failure the ANN index has no entry for this fact.

        The end state must be: vector_store has no entry (upsert returned False),
        ann_index has no entry.  If ANN had been written unconditionally the fact
        would be findable in-memory for the current session but invisible after
        a restart that rebuilds ANN from the vector store.
        """
        fact = _fact(fid="stuck-fact")
        ann_state: set[str] = set()
        vs_state: set[str] = set()

        class TrackingANN:
            def add(self, fid: str, emb: object) -> None:
                ann_state.add(fid)

        class FailingVS:
            available = True

            def upsert(self, fact_id: str, **_kw: object) -> bool:
                return False  # projection refused every time

        result = _upsert_fact_vectors(fact, "default", TrackingANN(), FailingVS())

        assert result is False, "returned True for a refused projection"
        assert "stuck-fact" not in ann_state, (
            "ANN has an entry for a fact whose projection was refused. "
            "After a restart (ANN rebuilt from vector store) this fact would "
            "vanish, creating a session-scoped ghost."
        )
        assert "stuck-fact" not in vs_state


# ---------------------------------------------------------------------------
# run_store_fact_direct — must use projection-first order
# ---------------------------------------------------------------------------


class TestRunStoreFactDirectWriteOrder:
    """run_store_fact_direct must attempt the vector store before the ANN index.

    The inline code in this function previously wrote ANN unconditionally before
    the vector store, meaning a failed VS upsert left an ANN ghost.
    """

    def _make_db_mock(self, *, store_fact_ok: bool = True) -> MagicMock:
        db = MagicMock()
        db.store_fact.return_value = None
        db.store_memory.return_value = None
        db.execute.return_value = []
        return db

    def test_ann_not_written_when_vs_refuses(self) -> None:
        """VS refuses → ANN must not receive the vector."""
        from superlocalmemory.core.store_pipeline import run_store_fact_direct

        fact = _fact(fid="direct-fact", embedding=[0.2] * 8)
        fact.memory_id = "mem-1"  # skip memory creation
        db = self._make_db_mock()
        ann = MagicMock()
        vs = MagicMock()
        vs.available = True
        vs.upsert.return_value = False  # refused

        run_store_fact_direct(
            fact=fact,
            profile_id="default",
            db=db,
            embedder=None,
            entity_resolver=None,
            ann_index=ann,
            graph_builder=None,
            retrieval_engine=None,
            vector_store=vs,
        )

        ann.add.assert_not_called()
        vs.upsert.assert_called_once()

    def test_vs_called_before_ann_on_success(self) -> None:
        """VS upsert must be called before ANN add."""
        from superlocalmemory.core.store_pipeline import run_store_fact_direct

        call_log: list[str] = []

        fact = _fact(fid="direct-ok", embedding=[0.2] * 8)
        fact.memory_id = "mem-1"
        db = self._make_db_mock()

        ann = MagicMock()
        ann.add.side_effect = lambda *a, **kw: call_log.append("ann")

        vs = MagicMock()
        vs.available = True
        vs.upsert.side_effect = lambda **kw: call_log.append("vs") or True

        run_store_fact_direct(
            fact=fact,
            profile_id="default",
            db=db,
            embedder=None,
            entity_resolver=None,
            ann_index=ann,
            graph_builder=None,
            retrieval_engine=None,
            vector_store=vs,
        )

        assert call_log == ["vs", "ann"], (
            f"wrong call order: {call_log}. VS must be written before ANN."
        )
