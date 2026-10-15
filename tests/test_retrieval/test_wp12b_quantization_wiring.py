"""Persisted quantized-only facts participate in the production semantic path."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from superlocalmemory.core.config import PolarQuantConfig, SLMConfig
from superlocalmemory.core.engine_wiring import _init_quantization_aware_search, init_retrieval
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord, Mode


@pytest.fixture
def wired(tmp_path):
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.embedding = replace(config.embedding, dimension=16)
    config.quantization = replace(config.quantization, polar=PolarQuantConfig(
        dimension=16, rotation_matrix_path=str(tmp_path / "rotation.npy")))
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize(schema)
    vector_store = MagicMock(available=True)
    vector_store.search.return_value = []
    retrievals = []
    try:
        yield config, db, vector_store, retrievals
    finally:
        for retrieval in retrievals:
            retrieval.close(wait=True)
        db.close()


@pytest.mark.parametrize("bit_width", [8, 4, 2])
@pytest.mark.parametrize("with_vector_extension", [True, False])
def test_enabled_quantized_only_fact_is_used_by_wired_channel(
    wired, bit_width, with_vector_extension,
):
    config, db, vector_store, retrievals = wired
    vector_store = vector_store if with_vector_extension else None
    retrieval = init_retrieval(config, db, None, None, None, vector_store=vector_store)
    retrievals.append(retrieval)
    channel = retrieval._semantic
    qas = channel._qas
    assert qas is not None
    memory = MemoryRecord(content="Quantization is active.")
    db.store_memory(memory)
    fact = AtomicFact(memory_id=memory.memory_id, content=memory.content)
    db.store_fact(fact)
    db.execute("INSERT INTO fact_retention (fact_id, profile_id) VALUES (?, ?)",
               (fact.fact_id, "default"))
    query = np.random.default_rng(1).standard_normal(16)
    query /= np.linalg.norm(query)
    assert qas._quantized_store.compress_fact(fact.fact_id, "default", query, bit_width)
    assert db.get_fact(fact.fact_id, "default").embedding is None
    real_search = type(qas).search
    with patch.object(type(qas), "search", autospec=True) as used:
        used.side_effect = lambda self, **kw: real_search(self, **kw)
        results = channel.search(query.tolist(), "default")
    assert used.call_count == 1
    assert fact.fact_id in {fid for fid, _ in results}


def test_disabled_configuration_is_explicit(wired, caplog):
    config, db, vector_store, _retrievals = wired
    config.quantization = replace(config.quantization, enabled=False)
    with caplog.at_level("INFO"):
        assert _init_quantization_aware_search(vector_store, db, config) is None
    assert "disabled by configuration" in caplog.text


def test_initialization_failure_is_visible(wired, caplog):
    config, db, vector_store, _retrievals = wired
    with patch("superlocalmemory.math.polar_quant.PolarQuantEncoder",
               side_effect=ValueError("bad codec")):
        assert _init_quantization_aware_search(vector_store, db, config) is None
    assert "quantized tiers unavailable" in caplog.text.lower()


def test_unavailable_candidate_backend_is_visible(wired, caplog):
    config, db, vector_store, _retrievals = wired
    with patch("superlocalmemory.retrieval.canonical_vector_index.candidate_vector_source",
               return_value=None):
        assert _init_quantization_aware_search(vector_store, db, config) is None
    assert "quantized tiers unavailable" in caplog.text.lower()


@pytest.mark.parametrize("bit_width", [2, 4])
@pytest.mark.parametrize("damage", ["header", "prior_projection", "base", "legacy"])
def test_bad_row_never_hides_healthy_quantized_only_fact(wired, caplog, bit_width, damage):
    from superlocalmemory.core.config import QJLConfig
    from superlocalmemory.math.qjl import QJLEncoder

    config, db, _, retrievals = wired
    retrieval = init_retrieval(config, db, None, None, None, vector_store=None)
    retrievals.append(retrieval)
    qas = retrieval._semantic._qas
    store = qas._quantized_store
    query = np.random.default_rng(44).standard_normal(16)
    memory = MemoryRecord(content="Persisted quantized-only facts")
    db.store_memory(memory)
    for fid in ("healthy", "bad"):
        db.store_fact(AtomicFact(fact_id=fid, memory_id=memory.memory_id, content=fid))
        db.execute("INSERT INTO fact_retention (fact_id, profile_id) VALUES (?, ?)",
                   (fid, "default"))
        assert store.compress_fact(fid, "default", query, bit_width)
    qe = store.load("bad", "default")
    if damage == "base":
        db.execute("UPDATE polar_embeddings SET angle_indices=? WHERE fact_id='bad'", (b"TQ",))
    else:
        payload = b"QJL1broken"
        if damage == "prior_projection":
            payload = QJLEncoder(QJLConfig(projection_dim=64)).encode_residual(
                query - store._polar.decode(qe))
        elif damage == "legacy":
            payload = qe.qjl_bits[12:]
        db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='bad'", (payload,))
    expected = {"healthy"} if damage == "base" else {"healthy", "bad"}
    assert {fid for fid, _ in qas.search(query, "default")} == expected
    assert {fid for fid, _ in retrieval._semantic.search(query.tolist(), "default")} == expected
    if damage != "base":
        scores = dict(store.search(query, "default"))
        assert scores["bad"] == pytest.approx(store._polar.approximate_similarity(query, qe))
    assert "bad" in caplog.text and "repair" in caplog.text.lower()
