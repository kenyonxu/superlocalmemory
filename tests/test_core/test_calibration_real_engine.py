# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Calibration against a REAL engine, REAL RetrievalEngine, REAL config and DB.

Why this file exists. test_wp6_calibration_job.py and
test_pr253_calibration_cancel.py build their engine from SimpleNamespace
stand-ins, and a stand-in will happily answer any attribute the code asks it
for. That is how `engine._config.profile_id` (SLMConfig has none) and
`retrieval._channels` (RetrievalEngine keeps its channels as `_semantic`,
`_bm25`, ...) both shipped in 4.2.1 and failed every calibration on a real
install at 40/48 with "'RetrievalEngine' object has no attribute '_channels'".

Only the two model boundaries are replaced here: the local question generator
(an Ollama call) and the Laya worker exchange (a resident MLX process). The
judge object itself is the real LayaSufficiencyJudge.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from superlocalmemory.core.answer_calibration import CalibrationJob
from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.retrieval.judge_calibration_file import measured_thresholds
from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge
from superlocalmemory.storage.models import Mode

_WORD = re.compile(r"[a-z0-9_]+")
_SERVICES = 40


class _BagOfWordsEmbedder:
    """Deterministic embedder where shared words mean a high cosine.

    A hash-seeded random vector gives a question no similarity to its own
    source, so the source never lands in the candidate set and every labelled
    example is skipped as "missing source". Hashing words into buckets keeps
    the property calibration measures: a question about Service 7 is nearer
    Service 7's fact than to the other thirty-nine.
    """

    is_available = True

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "little") % self.dimension] += 1.0
        norm = np.linalg.norm(vector)
        return (vector / norm if norm else vector).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def compute_fisher_params(self, embedding):
        vector = np.asarray(embedding, dtype=np.float64)
        return vector.tolist(), np.ones(len(vector), dtype=np.float64).tolist()


def _real_engine(base_dir: Path) -> MemoryEngine:
    from tests.conftest import force_sync_enrichment

    config = SLMConfig.for_mode(Mode.A, base_dir=base_dir)
    config.retrieval.use_cross_encoder = False
    embedder = _BagOfWordsEmbedder(config.embedding.dimension)
    engine = MemoryEngine(config)
    with ExitStack() as stack:
        stack.enter_context(patch(
            "superlocalmemory.core.engine_wiring.init_embedder", return_value=embedder))
        engine.initialize()
    force_sync_enrichment(engine)
    for i in range(_SERVICES):
        engine.store(
            f"Service {i}: Alice Chen approved the checklist for rollout {i}.",
            session_id=f"s{i}",
        )
    return engine


def _warm_real_judge() -> LayaSufficiencyJudge:
    """The real judge class, marked warm, with only the worker exchange stubbed."""
    judge = LayaSufficiencyJudge(start=False)
    judge._ready = True

    def exchange(req, timeout_s):
        absent = "AbsentProject" in req["query"]
        return {"ok": True, "probabilities": [0.1 if absent else 0.9] * len(req["documents"])}

    judge._background_exchange = exchange
    return judge


@pytest.fixture
def real_engine(tmp_path):
    engine = _real_engine(tmp_path)
    engine._retrieval_engine._sufficiency_judge = _warm_real_judge()
    try:
        yield engine
    finally:
        engine.close()


def test_the_calibration_job_completes_on_a_real_engine(real_engine, tmp_path, monkeypatch):
    from superlocalmemory.core import calibration_examples

    def generate(model, fact, absent_subject="", stop=None):
        service = re.match(r"Service (\d+)", fact).group(1)
        subject = absent_subject or f"Service {service}"
        return {"question": f"Who approved the checklist for {subject}?",
                "answer_quote": "Alice Chen"}

    monkeypatch.setattr(calibration_examples, "local_question", generate)
    job = CalibrationJob()
    job.start(real_engine)
    job._thread.join(60)
    assert not job._thread.is_alive()
    status = job.status()
    assert status["state"] == "complete", status
    assert status["done"] == status["total"]
    written = measured_thresholds(tmp_path / "answer_check_calibration.json")
    assert ("laya", "sufficiency-set-v2") in written
    # The embedding floor is measured from the real semantic channel too, not
    # skipped: that is the half of the job the broken attribute used to kill.
    metrics = status["metrics"]
    assert metrics["embedding_status"] == "measured", metrics["embedding_status"]
    assert metrics["answered"] >= 28 and metrics["unanswered"] >= 14
    job.stop()


def test_every_engine_attribute_calibration_reads_exists_on_the_real_objects(real_engine):
    """The audit, as a test: each attribute path the calibration modules read."""
    engine = real_engine
    retrieval = engine._retrieval_engine
    assert isinstance(engine.profile_id, str)
    for path in ("_config.base_dir", "_config.embedding", "_config.retrieval",
                 "_config.retrieval.min_semantic_evidence", "_db", "_embedder",
                 "_retrieval_engine", "_retrieval_engine._sufficiency_judge",
                 "_retrieval_engine._semantic", "_retrieval_engine.recall"):
        target = engine
        for part in path.split("."):
            assert hasattr(target, part), f"engine.{path}: no attribute {part!r}"
            target = getattr(target, part)
    assert retrieval._semantic is not None, "Mode A must wire the semantic channel"
    assert not hasattr(retrieval, "_channels"), (
        "RetrievalEngine has no _channels; calibration must not read one")
    assert not hasattr(engine._config, "profile_id")
    for method in ("get_all_facts", "get_facts_by_ids"):
        assert callable(getattr(engine._db, method))
