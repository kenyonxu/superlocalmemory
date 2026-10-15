"""A snapshot cannot rank observations from its future."""
from types import SimpleNamespace

import pytest

from superlocalmemory.retrieval.temporal_validity_filter import (
    admit_correction_candidates,
    admit_correction_fusion_results,
)
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord


@pytest.fixture
def world(tmp_path):
    db = DatabaseManager(tmp_path / "memory.db")
    db.initialize(schema)
    for fid, created, valid in (
        ("past", "2026-01-01", "2026-01-01"),
        ("future-created", "2026-09-01", "2026-01-01"),
        ("future-valid", "2026-01-01", "2026-09-01"),
    ):
        db.store_memory(MemoryRecord(memory_id=fid, content=fid))
        db.store_fact(AtomicFact(fact_id=fid, memory_id=fid, content=fid, created_at=created))
        db.execute("UPDATE fact_temporal_validity SET system_created_at='2026-01-01', "
                   "valid_from=? WHERE fact_id=?", (valid, fid))
    yield db
    db.close()


def test_snapshot_excludes_future_created_and_future_valid(world):
    candidates = {"bm25": [("future-created", 1.0), ("future-valid", .99), ("past", .8)]}
    assert admit_correction_candidates(candidates, "default", world, as_of="2026-02-01") == {
        "bm25": [("past", .8)]}


def test_snapshot_rechecks_expanded_future_facts(world):
    fused = [SimpleNamespace(fact_id=fid) for fid in ("future-valid", "future-created", "past")]
    result = admit_correction_fusion_results(fused, "default", world, as_of="2026-02-01")
    assert [r.fact_id for r in result] == ["past"]


def test_snapshot_exact_boundary_is_inclusive(world):
    result = admit_correction_candidates({"bm25": [("past", 1)]}, "default", world,
                                         as_of="2026-01-01T00:00:00Z")
    assert result == {"bm25": [("past", 1)]}


def test_explicit_future_axis_cannot_relax_snapshot(world):
    result = admit_correction_candidates({"bm25": [("future-valid", 1)]}, "default", world,
                                         as_of="2026-02-01", valid_at="2026-10-01")
    assert result == {"bm25": []}
