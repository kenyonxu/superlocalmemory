# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Root conftest — shared fixtures for Phase 0 Safety Net.

Provides in-memory DB, mock embedder, Mode A config, and
engine-with-mock-deps fixtures used across all test modules.

V3.3.7: Added session-scoped worker cleanup to prevent orphaned
subprocess workers (reranker_worker, embedding_worker) from leaking
memory across parallel test runs. Each worker consumes 0.5-1.5 GB.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# V3.3.14: Windows CI fix — KeyboardInterrupt during daemon thread teardown.
# On Windows, when pytest exits, daemon threads (reranker warmup, maintenance
# scheduler, parent watchdog) trigger KeyboardInterrupt that kills the process.
# This hook runs BEFORE pytest's thread cleanup and terminates workers cleanly.
def pytest_sessionfinish(session, exitstatus):
    """Clean up all SLM subprocess workers before pytest exits."""
    try:
        from superlocalmemory.core.embeddings import _cleanup_all_embedding_services
        _cleanup_all_embedding_services()
    except Exception:
        pass
    try:
        from superlocalmemory.retrieval.reranker import _cleanup_all_rerankers
        _cleanup_all_rerankers()
    except Exception:
        pass
    # Join any SLM daemon threads to prevent Windows KeyboardInterrupt on exit
    import threading
    for t in threading.enumerate():
        if t.daemon and t.name in ("ce-warmup", "ce-init-warmup", "parent-watchdog"):
            try:
                t.join(timeout=2)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Session-scoped worker cleanup (prevents orphaned subprocess leak)
# ---------------------------------------------------------------------------

def _kill_orphaned_slm_workers() -> None:
    """Kill any orphaned SLM subprocess workers.

    Targets: reranker_worker, embedding_worker, recall_worker.
    These subprocesses each consume 0.5-1.5 GB and can orphan when
    tests crash, get interrupted, or when parallel agents run tests.

    Skipped on Windows where pkill is unavailable and signal handling
    differs (KeyboardInterrupt propagation causes false CI failures).
    """
    if os.name == "nt":
        return  # Windows: atexit + __del__ handlers are sufficient

    worker_patterns = [
        "superlocalmemory.core.reranker_worker",
        "superlocalmemory.core.embedding_worker",
        "superlocalmemory.core.recall_worker",
    ]
    for pattern in worker_patterns:
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


@pytest.fixture(autouse=True, scope="session")
def _prevent_heavy_model_loading():
    """Prevent ALL heavy ML model loading during tests.

    V3.4.11: Mock CrossEncoderReranker (spawns 130MB ONNX subprocess)
    and WorkerPool (spawns 930MB embedding subprocess). Without this,
    the full suite takes 20+ minutes. With it: under 2 minutes.

    Tests that explicitly need real models should patch these back.
    """
    from unittest.mock import MagicMock, patch as _patch

    mock_reranker = MagicMock()
    mock_reranker.rerank.return_value = None
    mock_reranker.warmup_sync.return_value = True
    mock_reranker._kill_worker = MagicMock()

    mock_pool = MagicMock()
    mock_pool.store.return_value = {"ok": True, "fact_ids": ["pending:mock"], "count": 1}
    mock_pool.recall.return_value = {"ok": True, "results": [], "count": 0}
    mock_pool.kill.return_value = None

    patches = [
        _patch(
            "superlocalmemory.retrieval.reranker.CrossEncoderReranker",
            return_value=mock_reranker,
        ),
        _patch(
            "superlocalmemory.core.worker_pool.WorkerPool.shared",
            return_value=mock_pool,
        ),
    ]
    for p in patches:
        p.start()

    yield

    for p in patches:
        p.stop()

    try:
        _kill_orphaned_slm_workers()
    except (KeyboardInterrupt, Exception):
        pass


@pytest.fixture(autouse=True)
def cleanup_slm_workers_between_tests():
    """Kill SLM subprocess workers after EACH test to prevent memory pileup.

    V3.3.12: Safety net — if any test bypasses the session mock and creates
    real workers, this cleans them up. Lightweight when no workers exist.
    """
    yield
    try:
        from superlocalmemory.core.embeddings import _cleanup_all_embedding_services
        _cleanup_all_embedding_services()
    except Exception:
        pass
    try:
        from superlocalmemory.retrieval.reranker import _cleanup_all_rerankers
        _cleanup_all_rerankers()
    except Exception:
        pass


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database with full SLM schema.

    Returns a real sqlite3 Connection backed by :memory:.
    Gives real SQL execution without touching disk.
    """
    from superlocalmemory.storage import schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create_all_tables(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def mock_embedder():
    """Mock embedder that returns deterministic 768-dim vectors.

    Uses seeded RNG keyed on input string for reproducibility.
    Implements: embed(), is_available, compute_fisher_params().
    """
    emb = MagicMock()

    def _embed(text: str) -> list[float]:
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(768).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()

    emb.embed.side_effect = _embed
    emb.is_available = True
    emb.compute_fisher_params.return_value = ([0.0] * 768, [1.0] * 768)
    return emb


@pytest.fixture
def mode_a_config(tmp_path):
    """SLMConfig for Mode A using tmp_path as base_dir."""
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.storage.models import Mode

    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    # V3.4.11: Disable cross-encoder in tests — spawning a real reranker
    # subprocess per test adds 3s teardown overhead (kills ONNX worker).
    # 13 engine tests × 3s = 39s wasted. Cross-encoder has its own tests.
    config.retrieval.use_cross_encoder = False
    return config


@pytest.fixture
def engine_with_mock_deps(mode_a_config, mock_embedder, tmp_path):
    """A MemoryEngine with mocked LLM and embedder for fast unit tests.

    Initializes with real DB (on disk in tmp_path) and real schema,
    but mocked embeddings and no LLM. Suitable for testing store/recall
    flow without heavy ML dependencies.
    """
    from superlocalmemory.core.engine import MemoryEngine

    engine = MemoryEngine(mode_a_config)

    # Patch embedder initialization to use our mock
    with patch('superlocalmemory.core.engine_wiring.init_embedder', return_value=mock_embedder):
        engine.initialize()
        engine._embedder = mock_embedder

    yield engine
    engine.close()
