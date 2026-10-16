# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Recall abstains on nonsense with the real built-in model (slow: loads Nomic).

Every other evidence-floor test hands the engine channel scores by hand, so none
of them ever saw the real score distribution: the built-in model gives unrelated
sentences a cosine of 0.30-0.48, which the old floor (effectively raw 0.20) let
through. This test stores six memories in Mode A, asks three nonsense questions
and one real one, and requires an empty, flagged answer for the nonsense and the
right memory for the real question.

Run with: pytest -m slow tests/test_retrieval/test_recall_abstains_on_nonsense_with_the_real_model.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

MODEL_CACHE = "models--nomic-ai--nomic-embed-text-v1.5"
NONSENSE = [
    "zxqv plorth wimble",
    "purple elephants juggling quantum bananas",
    "asdf qwer zxcv uiop",
]
MEMORIES = [
    "Varun decided to ship SuperLocalMemory 4.1.25 after the full suite passed on Friday.",
    "The dashboard favicon was replaced with the caped mascot in October.",
    "Postgres connection pooling with pgbouncer reduced latency by thirty percent in staging.",
    "My daughter likes pancakes with maple syrup on Sunday mornings before school.",
    "The quarterly tax filing deadline for the company is the fifteenth of the month.",
    "AgentAssert is the behavioral assertion library; AgentAssay is the evaluation and scoring framework.",
]


def _real_hf_home() -> Path:
    """The isolation plugin moves HOME; the model cache lives under the real one."""
    real_data = os.environ.get("SLM_TEST_REAL_DATA_ROOT")
    home = Path(real_data).parent if real_data else Path.home()
    return Path(os.environ.get("HF_HOME") or home / ".cache" / "huggingface")


def test_nonsense_questions_abstain_and_a_real_question_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hf_home = _real_hf_home()
    if not (hf_home / "hub" / MODEL_CACHE).is_dir():
        pytest.skip(f"built-in embedding model is not cached under {hf_home}")
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv("HF_HUB_CACHE", str(hf_home / "hub"))
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.core.engine import MemoryEngine
    from superlocalmemory.core.engine_ingestion import canonical_store, local_trusted_actor_id
    from superlocalmemory.storage.models import Mode

    cfg = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    cfg.retrieval.use_cross_encoder = False
    engine = MemoryEngine(cfg)
    engine.initialize()
    try:
        actor = local_trusted_actor_id("python-api")
        for text in MEMORIES:
            canonical_store(engine, text, source_type="python-api",
                            trusted_actor_id=actor, require_complete=True)
        assert engine.fact_count >= len(MEMORIES)

        for question in NONSENSE:
            response = engine.recall(question, limit=5)
            assert response.results == [], (
                f"{question!r} returned {[r.fact.content[:40] for r in response.results]}")
            assert response.no_confident_match is True

        response = engine.recall("what does my daughter like for breakfast?", limit=5)
        assert response.results, "a real question must still be answered"
        assert "pancakes" in response.results[0].fact.content
        assert response.no_confident_match is False
    finally:
        engine.close()
