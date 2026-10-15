# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Hub baselines: fixed probes, stored per fact and space, filled resumably."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from superlocalmemory.retrieval import hub_baseline as hb
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import M061_fact_hub_baseline as M061
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "answerability"


def test_probe_set_is_fixed_unique_and_versioned() -> None:
    probes = hb.probe_texts()
    assert len(probes) == 96 and len(set(probes)) == 96
    assert probes == hb.probe_texts()
    with pytest.raises(ValueError):
        hb.probe_texts("hub-probes-v0")


def test_probes_share_nothing_with_the_measured_questions() -> None:
    # A probe equal to an evaluation question would grade its own homework.
    asked = set()
    for name in ("nonsense", "real", "unanswerable"):
        for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                asked.add(json.loads(line)["question"].lower())
    assert not asked & {p.lower() for p in hb.probe_texts()}


def test_baselines_are_mean_and_top_k_mean() -> None:
    probes = hb._unit_rows([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    (mean, top1), = hb.baselines([[1.0, 0.0]], probes, top_k=1)
    sims = [1.0, 0.0, 2 ** -0.5]
    assert mean == pytest.approx(sum(sims) / 3, abs=1e-6)
    assert top1 == pytest.approx(1.0, abs=1e-6)
    assert hb.baselines([], probes) == []


class _Embedder:
    """Deterministic 4-d vectors; counts how often probes are embedded."""

    def __init__(self, model="test/model", dim=4):
        from superlocalmemory.core.config import EmbeddingConfig

        self._config = EmbeddingConfig(model_name=model, dimension=dim)
        self.batches = 0

    def embed_batch(self, texts):
        self.batches += 1
        out = []
        for t in texts:
            h = abs(hash(("probe", t))) % 1000
            out.append([1.0, h / 1000.0, 0.5, 0.0])
        return out


@pytest.fixture()
def db(tmp_path):
    manager = DatabaseManager(tmp_path / "memory.db")
    manager.initialize(schema)
    conn = sqlite3.connect(tmp_path / "memory.db")
    M061.apply(conn)
    conn.commit()
    conn.close()
    hb.PROBES._cache.clear()
    yield manager
    manager.close()


def _store(db, n, *, vector=lambda i: [0.0, 1.0, float(i), 1.0]):
    ids = []
    for i in range(n):
        memory = MemoryRecord(memory_id=f"m{i}", content=f"note {i}")
        db.store_memory(memory)
        ids.append(db.store_fact(AtomicFact(fact_id=f"f{i}", memory_id=memory.memory_id,
                                            content=f"note {i}", embedding=vector(i))))
    return ids


def test_save_time_compute_then_read_for_the_live_space(db) -> None:
    emb = _Embedder()
    ids = _store(db, 3)
    assert hb.compute_for_facts(db, emb, ids) == 3
    got = hb.read(db, ids, hb.space_of(emb))
    assert set(got) == set(ids)
    for topk, mean in got.values():
        assert topk >= mean
    assert hb.read(db, ids, "other/model::4") == {}
    assert hb.compute_for_facts(db, emb, ids) == 3  # upsert, no duplicate rows
    assert db.execute(f"SELECT COUNT(*) AS n FROM {hb.TABLE}")[0]["n"] == 3
    assert emb.batches == 1  # probes embedded once per process and space


def test_backfill_is_bounded_and_resumable(db) -> None:
    emb = _Embedder()
    _store(db, 7)
    assert hb.backfill_step(db, emb, batch=3) == 3
    assert len(hb.pending(db, hb.space_of(emb), 100)) == 4
    # A stop between steps loses nothing: a new pass picks up the remainder.
    assert hb.Backfill(db, emb, batch=3).run_until_done() == 4
    assert hb.backfill_step(db, emb, batch=3) == 0


def test_a_new_space_recomputes_and_vectors_of_another_width_are_skipped(db) -> None:
    _store(db, 2)
    hb.Backfill(db, _Embedder()).run_until_done()
    wider = _Embedder(model="other/model", dim=8)
    wider.embed_batch = lambda texts: [[1.0] * 8 for _ in texts]
    assert hb.backfill_step(db, wider) == 0  # stored vectors are 4-d: nothing to do
    assert hb.read(db, ["f0"], hb.space_of(wider)) == {}


def test_deleting_a_fact_deletes_its_baseline(db) -> None:
    emb = _Embedder()
    ids = _store(db, 2)
    hb.compute_for_facts(db, emb, ids)
    db.execute("DELETE FROM atomic_facts WHERE fact_id = ?", (ids[0],))
    assert set(hb.read(db, ids, hb.space_of(emb))) == {ids[1]}


def test_missing_table_and_embedder_fail_open(tmp_path) -> None:
    manager = DatabaseManager(tmp_path / "memory.db")
    manager.initialize(schema)
    try:
        assert hb.read(manager, ["f0"], "x::4") == {}
        assert hb.compute_for_facts(manager, _Embedder(), ["f0"]) == 0
        assert hb.backfill_step(manager, _Embedder()) == 0
        assert hb.compute_for_facts(manager, None, ["f0"]) == 0
        assert hb.space_of(object()) is None
    finally:
        manager.close()


def test_a_pass_is_bounded_by_batches_and_stops_when_asked(db) -> None:
    emb = _Embedder()
    _store(db, 5)
    task = hb.Backfill(db, emb, batch=2)
    assert task.run_until_done(max_batches=1) == 2 and not task.finished
    assert task.run_until_done() == 5 and task.finished
    stopped = hb.Backfill(db, emb, batch=2)
    stopped.stop()
    assert stopped.run_until_done() == 0


def test_probe_vectors_are_unit_length() -> None:
    m = hb._unit_rows([[3.0, 4.0], [0.0, 0.0]])
    assert np.allclose(np.linalg.norm(m[0]), 1.0)
    assert np.allclose(m[1], 0.0)


def test_unusable_rows_never_stall_the_backfill(db) -> None:
    # The first rows hold vectors of another width; the pass steps past them.
    _store(db, 6, vector=lambda i: [1.0] * 8 if i < 3 else [0.0, 1.0, float(i), 1.0])
    emb = _Embedder()
    assert hb.Backfill(db, emb, batch=2).run_until_done() == 3
    assert set(hb.read(db, ["f3", "f4", "f5"], hb.space_of(emb))) == {"f3", "f4", "f5"}
