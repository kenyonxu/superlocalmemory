# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A stored memory gets its hub baseline at save; maintenance fills the rest."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.core.config import EmbeddingConfig, SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.retrieval import hub_baseline as hb
from superlocalmemory.storage.models import Mode

DIM = 768


def _vector(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [((seed[i % 32] + i) % 17) / 17.0 + 0.01 for i in range(DIM)]


def _embedder() -> MagicMock:
    m = MagicMock()
    m._config = EmbeddingConfig(model_name="nomic-ai/nomic-embed-text-v1.5", dimension=DIM)
    m.is_available = True
    m.embed.side_effect = _vector
    m.embed_batch.side_effect = lambda texts: [_vector(t) for t in texts]
    m.compute_fisher_params.side_effect = lambda v: (v, [1.0] * len(v))
    return m


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[MemoryEngine]:
    hb.PROBES._cache.clear()
    eng = MemoryEngine(SLMConfig(
        mode=Mode.A, base_dir=tmp_path, db_path=tmp_path / "memory.db",
        active_profile="default",
        embedding=EmbeddingConfig(model_name="nomic-ai/nomic-embed-text-v1.5", dimension=DIM)))
    eng._config.retrieval.use_cross_encoder = False
    with patch("superlocalmemory.core.engine_wiring.init_embedder", return_value=_embedder()):
        eng._ensure_init()
    yield eng
    eng.close()


def test_a_stored_memory_has_its_baseline_before_store_returns(engine) -> None:
    fact_ids = engine.store("The nightly backup keeps seven copies.")
    assert fact_ids
    got = hb.read(engine._db, fact_ids, hb.space_of(engine._embedder))
    assert set(got) == set(fact_ids)


def test_maintenance_fills_baselines_a_store_does_not_have(engine) -> None:
    fact_ids = engine.store("Release notes are written before tagging.")
    conn = sqlite3.connect(engine._config.db_path)
    conn.execute(f"DELETE FROM {hb.TABLE}")  # a store from before this release
    conn.commit()
    conn.close()
    space = hb.space_of(engine._embedder)
    assert hb.read(engine._db, fact_ids, space) == {}
    scheduler = engine._maintenance_scheduler
    assert scheduler is not None and scheduler._initial_hub_timer is not None
    scheduler._hub_baseline_pass()
    assert set(hb.read(engine._db, fact_ids, space)) == set(fact_ids)
