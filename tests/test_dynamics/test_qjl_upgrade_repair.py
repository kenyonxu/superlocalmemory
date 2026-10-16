"""Legacy sketches need repair even when EAP retains the same precision."""

from contextlib import closing
from dataclasses import replace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from superlocalmemory.core.config import PolarQuantConfig, SLMConfig
from superlocalmemory.core.engine_wiring import init_retrieval
from superlocalmemory.dynamics.eap_scheduler import EAPScheduler
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord, Mode


@pytest.fixture
def legacy_store(tmp_path):
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.embedding = replace(config.embedding, dimension=16)
    config.quantization = replace(config.quantization, polar=PolarQuantConfig(
        dimension=16, rotation_matrix_path=str(tmp_path / "rotation.npy")))
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    retrieval = init_retrieval(config, db, None, None, None, vector_store=None)
    store = retrieval._semantic._qas._quantized_store
    vector = np.random.default_rng(40).standard_normal(16)
    memory = MemoryRecord(content="Legacy upgrade fixture")
    db.store_memory(memory)
    for fid in ("repairable", "no-original"):
        db.store_fact(AtomicFact(fact_id=fid, memory_id=memory.memory_id, content=fid,
                                embedding=vector.tolist() if fid == "repairable" else None))
        db.execute("INSERT INTO fact_retention (fact_id, profile_id, retention_score) "
                   "VALUES (?, 'default', 0.3)", (fid,))
        assert store.compress_fact(fid, "default", vector, 4)
        bits = store.load(fid, "default").qjl_bits[12:]
        db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id=?", (bits, fid))
    try:
        yield db, store, EAPScheduler(db, MagicMock(), store, config.quantization), config
    finally:
        retrieval.close(wait=True)
        db.close()


def test_normal_cycle_repairs_unchanged_precision_and_is_idempotent(legacy_store):
    db, store, scheduler, _ = legacy_store
    sql = "SELECT embedding FROM atomic_facts WHERE fact_id='repairable'"
    original = bytes(db.execute(sql)[0][0])
    preview = scheduler.run_eap_cycle("default", dry_run=True)
    assert preview["repair_needed"] == 2 and preview["unrecoverable"] == 1
    assert not store.load("repairable", "default").qjl_bits.startswith(b"QJL1")
    result = scheduler.run_eap_cycle("default")
    assert result["repaired"] == 1 and result["unrecoverable"] == 1
    assert store.load("repairable", "default").qjl_bits.startswith(b"QJL1")
    assert scheduler.run_eap_cycle("default")["repaired"] == 0
    assert bytes(db.execute(sql)[0][0]) == original


def test_interrupted_repair_rolls_back_one_row_and_can_resume(legacy_store):
    db, store, scheduler, _ = legacy_store
    before = store.load("repairable", "default").qjl_bits
    execute = db.execute
    def interrupt(sql, params=()):
        if sql.startswith("UPDATE embedding_quantization_metadata"):
            raise KeyboardInterrupt
        return execute(sql, params)
    with patch.object(db, "execute", side_effect=interrupt), pytest.raises(KeyboardInterrupt):
        scheduler.run_eap_cycle("default")
    assert store.load("repairable", "default").qjl_bits == before
    assert scheduler.run_eap_cycle("default")["repaired"] == 1


@pytest.mark.parametrize("dimension_mismatch", [True, False])
def test_doctor_reports_legacy_and_unrecoverable_counts(
    legacy_store, monkeypatch, dimension_mismatch,
):
    from argparse import Namespace

    from tests.helpers.doctor import run_doctor_report

    db, _, _, config = legacy_store
    if dimension_mismatch:
        config.quantization = replace(config.quantization, polar=replace(
            config.quantization.polar, dimension=768))
    monkeypatch.setenv("SLM_DATA_DIR", str(db.db_path.parent))
    captured = []
    with patch.object(SLMConfig, "load", return_value=config), patch(
        "superlocalmemory.cli.json_output.json_print",
        side_effect=lambda event, data=None, **kw: captured.append(data),
    ):
        run_doctor_report(Namespace(json=True, quick=True))
    row = next(row for row in captured[-1]["checks"] if row["name"] == "QJL sketches")
    assert row["status"] == "WARN"
    assert "2" in row["detail"] and "1" in row["detail"]
    assert "slm quantize --repair-sketches --execute" in row["fix"] and "re-embed" in row["fix"]


@pytest.mark.parametrize("payload", [b"QJL1broken", b"QJL1" + b"\0" * 16, None, b""])
def test_incompatible_or_missing_sketch_is_repaired(legacy_store, payload):
    db, store, scheduler, _ = legacy_store
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='repairable'", (payload,))
    assert scheduler.run_eap_cycle("default")["repaired"] == 1
    assert store.load("repairable", "default").qjl_bits.startswith(b"QJL1")


def test_other_profile_is_not_repaired(legacy_store):
    _, store, scheduler, _ = legacy_store
    assert scheduler.run_eap_cycle("other")["repair_needed"] == 0
    assert not store.load("repairable", "default").qjl_bits.startswith(b"QJL1")


def test_bad_base_does_not_abort_other_repairs(legacy_store):
    db, store, scheduler, _ = legacy_store
    db.execute("UPDATE atomic_facts SET embedding=(SELECT embedding FROM atomic_facts "
               "WHERE fact_id='repairable') WHERE fact_id='no-original'")
    db.execute("UPDATE polar_embeddings SET angle_indices=? WHERE fact_id='repairable'", (b"",))
    result = scheduler.run_eap_cycle("default")
    assert result["repair_errors"] == 1 and result["repaired"] == 1
    assert store.load("no-original", "default").qjl_bits.startswith(b"QJL1")


def test_concurrent_base_change_does_not_write_stale_correction(legacy_store):
    import sqlite3

    db, store, scheduler, _ = legacy_store
    before = store.load("repairable", "default").qjl_bits
    execute = db.execute
    def race(sql, params=()):
        if sql.startswith("UPDATE polar_embeddings SET qjl_bits"):
            with closing(sqlite3.connect(db.db_path)) as other, other:
                other.execute("UPDATE polar_embeddings SET radius=radius+1 "
                              "WHERE fact_id='repairable'")
        return execute(sql, params)
    with patch.object(db, "execute", side_effect=race):
        result = scheduler.run_eap_cycle("default")
    assert result["repair_errors"] == 1 and result["repaired"] == 0
    assert store.load("repairable", "default").qjl_bits == before


@pytest.mark.parametrize("use_json", [True, False])
@pytest.mark.parametrize("dimension_mismatch", [True, False])
def test_normal_quantize_command_repairs_and_reports(
    legacy_store, use_json, dimension_mismatch, capsys,
):
    from argparse import Namespace

    from superlocalmemory.cli.commands import cmd_quantize

    db, store, _, config = legacy_store
    if dimension_mismatch:
        config.quantization = replace(config.quantization, polar=replace(
            config.quantization.polar, dimension=768))
    engine = MagicMock(_db=db, profile_id="default")
    captured = []
    with patch.object(SLMConfig, "load", return_value=config), patch(
        "superlocalmemory.core.engine.MemoryEngine", return_value=engine,
    ), patch("superlocalmemory.core.admission.gate_cli_mutation"), patch(
        "superlocalmemory.cli.json_output.json_print",
        side_effect=lambda event, data=None, **kw: captured.append(data),
    ):
        cmd_quantize(Namespace(json=use_json, dry_run=False, profile="default"))
    assert store.load("repairable", "default").qjl_bits.startswith(b"QJL1")
    if use_json:
        assert captured[0]["repaired"] == 1 and captured[0]["unrecoverable"] == 1
    else:
        assert "QJL repaired:      1" in capsys.readouterr().out


def test_repair_uses_effective_codec_dimension(legacy_store):
    _, _, scheduler, _ = legacy_store
    scheduler._config = replace(scheduler._config, polar=replace(
        scheduler._config.polar, dimension=768))
    assert scheduler.run_eap_cycle("default")["repaired"] == 1


def test_doctor_classifies_prior_configuration_header_before_legacy_size(legacy_store):
    from superlocalmemory.cli.doctor_quantization import qjl_finding
    from superlocalmemory.math.qjl import QJLEncoder
    from superlocalmemory.storage.qjl_repair import repair_status

    db, _, scheduler, config = legacy_store
    prior = QJLEncoder(replace(config.quantization.qjl, projection_dim=32))
    payload = prior.encode_residual(np.ones(16))
    assert payload.startswith(b"QJL1")
    assert len(payload) == (config.quantization.qjl.projection_dim + 7) // 8
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='repairable'", (payload,))
    assert repair_status(db, "default", config.quantization) == {
        "repair_needed": 2, "legacy": 1, "incompatible": 1, "unrecoverable": 1,
    }
    with patch.object(SLMConfig, "load", return_value=config):
        status, detail, fix = qjl_finding(db.db_path)
    assert status == "WARN"
    assert detail == "2 need repair (1 legacy, 1 incompatible); 1 need re-embedding"
    assert "slm quantize --repair-sketches --execute" in fix
    assert scheduler.run_eap_cycle("default")["repaired"] == 1


@pytest.mark.parametrize(("payload", "storage_type"), [
    ("QJL1broken", "text"), ("x" * 16, "text"), ("", "text"),
    (16, "integer"), (0, "integer"), (16.0, "real"),
])
def test_nonbinary_sketch_does_not_abort_diagnostics_or_other_repairs(
    legacy_store, payload, storage_type,
):
    from superlocalmemory.storage.qjl_repair import repair_status

    db, store, scheduler, config = legacy_store
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='no-original'", (payload,))
    assert db.execute("SELECT typeof(qjl_bits) FROM polar_embeddings "
                      "WHERE fact_id='no-original'")[0][0] == storage_type
    assert repair_status(db, "default", config.quantization) == {
        "repair_needed": 2, "legacy": 1, "incompatible": 1, "unrecoverable": 1,
    }
    result = scheduler.run_eap_cycle("default")
    assert result["repaired"] == 1 and result["unrecoverable"] == 1
    assert result["repair_errors"] == 0
    assert store.load("repairable", "default").qjl_bits.startswith(b"QJL1")


@pytest.mark.parametrize(("payload", "storage_type"), [
    ("QJL1broken", "text"), ("x" * 16, "text"), (16.0, "real"),
])
def test_nonbinary_sketch_with_original_repairs_and_is_idempotent(
    legacy_store, payload, storage_type,
):
    from superlocalmemory.storage.qjl_repair import original_vector

    db, store, scheduler, _ = legacy_store
    base_sql = ("SELECT radius, angle_indices, bit_width FROM polar_embeddings "
                "WHERE fact_id='repairable'")
    source_sql = "SELECT embedding FROM atomic_facts WHERE fact_id='repairable'"
    size_sql = ("SELECT compressed_size_bytes FROM embedding_quantization_metadata "
                "WHERE fact_id='repairable'")
    base_before = tuple(db.execute(base_sql)[0])
    source_before = db.execute(source_sql)[0][0]
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='repairable'", (payload,))
    assert db.execute("SELECT typeof(qjl_bits) FROM polar_embeddings "
                      "WHERE fact_id='repairable'")[0][0] == storage_type
    db.execute("UPDATE embedding_quantization_metadata SET compressed_size_bytes=17 "
               "WHERE fact_id='repairable'")

    result = scheduler.run_eap_cycle("default")
    assert result["repaired"] == 1 and result["repair_errors"] == 0
    assert result["unrecoverable"] == 1
    qe = store.load("repairable", "default")
    vector = original_vector(db, "repairable", "default", 16)
    assert qe.qjl_bits.startswith(b"QJL1")
    assert qe.qjl_bits == store._qjl.encode_residual(vector - store._polar.decode(qe))
    size = len(qe.angle_indices) + len(qe.qjl_bits)
    assert db.execute(size_sql)[0][0] == size
    assert tuple(db.execute(base_sql)[0]) == base_before
    assert db.execute(source_sql)[0][0] == source_before

    next_cycle = scheduler.run_eap_cycle("default")
    assert next_cycle["repaired"] == 0 and next_cycle["repair_errors"] == 0
    assert next_cycle["repair_needed"] == 1
    assert store.load("repairable", "default").qjl_bits == qe.qjl_bits
    assert db.execute(size_sql)[0][0] == size


@pytest.mark.parametrize("payload", ["QJL1broken", "x" * 16, 16.0])
def test_nonbinary_sketch_writer_conflict_preserves_value_and_metadata(legacy_store, payload):
    import sqlite3

    db, store, scheduler, _ = legacy_store
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='repairable'", (payload,))
    size_sql = ("SELECT compressed_size_bytes FROM embedding_quantization_metadata "
                "WHERE fact_id='repairable'")
    db.execute("UPDATE embedding_quantization_metadata SET compressed_size_bytes=17 "
               "WHERE fact_id='repairable'")
    execute = db.execute
    raced = False
    replacement = "new corrupt sketch"

    def race(sql, params=()):
        nonlocal raced
        if sql.startswith("UPDATE polar_embeddings SET qjl_bits"):
            assert not raced
            with closing(sqlite3.connect(db.db_path)) as other, other:
                other.execute("UPDATE polar_embeddings SET qjl_bits=? "
                              "WHERE fact_id='repairable'", (replacement,))
            raced = True
        return execute(sql, params)

    with patch.object(db, "execute", side_effect=race):
        result = scheduler.run_eap_cycle("default")
    assert raced and result["repaired"] == 0 and result["repair_errors"] == 1
    assert db.execute("SELECT qjl_bits FROM polar_embeddings "
                      "WHERE fact_id='repairable'")[0][0] == replacement
    assert db.execute(size_sql)[0][0] == 17
    assert scheduler.run_eap_cycle("default")["repaired"] == 1
    qe = store.load("repairable", "default")
    assert db.execute(size_sql)[0][0] == len(qe.angle_indices) + len(qe.qjl_bits)
    assert scheduler.run_eap_cycle("default")["repaired"] == 0


@pytest.mark.parametrize(("column", "value"), [
    ("angle_indices", b""), ("radius", float("inf")), ("radius", "broken"),
])
def test_nonbinary_sketch_does_not_hide_invalid_base(legacy_store, column, value):
    db, _, scheduler, _ = legacy_store
    payload = "QJL1broken"
    db.execute("UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id='repairable'", (payload,))
    # Column names are fixed test parameters; persisted values remain bound.
    db.execute(f"UPDATE polar_embeddings SET {column}=? WHERE fact_id='repairable'", (value,))
    size_sql = ("SELECT compressed_size_bytes FROM embedding_quantization_metadata "
                "WHERE fact_id='repairable'")
    before = db.execute(size_sql)[0][0]
    result = scheduler.run_eap_cycle("default")
    assert result["repaired"] == 0 and result["repair_errors"] == 1
    assert db.execute("SELECT qjl_bits FROM polar_embeddings "
                      "WHERE fact_id='repairable'")[0][0] == payload
    assert db.execute(size_sql)[0][0] == before
