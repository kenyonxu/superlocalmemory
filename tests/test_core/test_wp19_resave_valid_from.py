"""A duplicate re-save never replaces an event start already on record (review M2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from superlocalmemory.core.config import Mode, RetrievalConfig, SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from tests.test_core.test_a_dated_fact_reaches_the_temporal_layer import _MockEmbedder


@pytest.fixture()
def engine(tmp_path: Path) -> MemoryEngine:
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.db_path = tmp_path / 'memory.db'
    config.retrieval = RetrievalConfig(use_cross_encoder=False, agentic_max_rounds=0)
    eng = MemoryEngine(config)
    with patch('superlocalmemory.core.embeddings.EmbeddingService',
               return_value=_MockEmbedder(768)):
        eng.initialize()
    from tests.conftest import force_sync_enrichment
    yield force_sync_enrichment(eng)
    eng.close()


def _starts(db: Path) -> dict[str, str]:
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        return dict(conn.execute('SELECT fact_id, valid_from FROM fact_temporal_validity'))


def test_duplicate_resave_keeps_a_corrected_event_start(engine) -> None:
    db = Path(engine._config.db_path)
    text = 'Zephyr moved the billing jobs to PostgreSQL.'
    engine.store(content=text, session_date='2026-03-14')
    assert _starts(db)
    # A correction (or an earlier extraction) set the real event start.
    engine._db.execute("UPDATE fact_temporal_validity SET valid_from='2019-06-01'")
    engine.store(content=text, session_date='2026-04-20')
    starts = _starts(db)
    assert starts and set(starts.values()) == {'2019-06-01'}, starts
