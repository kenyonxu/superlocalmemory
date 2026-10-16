# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Production recall opens a source store before reaching the fidelity guard."""

from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import pytest

from superlocalmemory.core import recall_pipeline
from superlocalmemory.core.engine import MemoryEngine
from superlocalmemory.core.engine_capabilities import Capabilities, CapabilityError
from superlocalmemory.core.kind_reconcile_task import KindReconcileTask
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import (
    AtomicFact,
    MemoryRecord,
    Mode,
    RecallResponse,
    RetrievalResult,
)


@pytest.mark.parametrize("mode", [Mode.A, Mode.B, Mode.C])
def test_first_recall_opens_the_source_store(mode, mode_a_config, monkeypatch):
    config = replace(mode_a_config, mode=mode)
    response = RecallResponse()

    def wire_retrieval(engine):
        assert isinstance(engine._db, DatabaseManager)
        record = MemoryRecord(content="The release requires owner approval.")
        engine._db.store_memory(record)
        fact = AtomicFact(memory_id=record.memory_id, content=record.content)
        engine._db.store_fact(fact)
        response.results = [RetrievalResult(fact=fact, score=0.9)]
        engine._retrieval_engine = SimpleNamespace(
            _sufficiency_judge=None, recall=lambda *a, **k: response,
        )

    monkeypatch.setattr(MemoryEngine, "_init_heavy_layer", wire_retrieval)
    monkeypatch.setattr(MemoryEngine, "_process_pending_memories", lambda self: None)
    monkeypatch.setattr(KindReconcileTask, "start", lambda self: None)
    monkeypatch.setattr(recall_pipeline, "apply_ranking", lambda r, *a, **k: r)
    with closing(MemoryEngine(config)) as engine:
        assert engine._db is None
        out = engine.recall("release approval", fast=True)
        assert engine._db.db_path == config.db_path
        assert [r.fact.content for r in out.results] == ["The release requires owner approval."]
        assert out.source_fidelity_status == "checked"


def test_store_open_failure_never_reaches_the_pipeline(mode_a_config, monkeypatch):
    entered = []

    def cannot_open(engine):
        raise RuntimeError("test store unavailable")

    monkeypatch.setattr(MemoryEngine, "_init_db_layer", cannot_open)
    monkeypatch.setattr(recall_pipeline, "run_recall", lambda *a, **k: entered.append(True))
    with closing(MemoryEngine(mode_a_config)) as engine:
        with pytest.raises(RuntimeError, match="test store unavailable"):
            engine.recall("release")
    assert entered == []


def test_light_engine_rejects_direct_recall_without_opening_store(mode_a_config):
    with closing(MemoryEngine(mode_a_config, capabilities=Capabilities.LIGHT)) as engine:
        with pytest.raises(CapabilityError, match="requires a FULL"):
            engine.recall("release")
        assert engine._db is None
