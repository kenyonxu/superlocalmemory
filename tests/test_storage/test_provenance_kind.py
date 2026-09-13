# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""provenance_kind: controlled vocabulary, storage column, DB filtering.

Governance gating tag on the retrieval unit (deepmaid M3b shared-layer
governance). The vocabulary is closed — ``world`` / ``private`` / ``curated``
/ ``legacy`` — and ``None`` means "not yet tagged", never "world".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from superlocalmemory.storage import schema as real_schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import (
    M052_provenance_kind_column as M052,
)
from superlocalmemory.storage.models import (
    PROVENANCE_KINDS,
    AtomicFact,
    MemoryRecord,
    validate_provenance_kind,
)


def _db(tmp_path: Path) -> DatabaseManager:
    mgr = DatabaseManager(tmp_path / "t.db")
    mgr.initialize(real_schema)
    return mgr


class TestVocabulary:
    def test_four_values(self):
        assert PROVENANCE_KINDS == frozenset({"world", "private", "curated", "legacy"})

    def test_validates_in_vocabulary(self):
        for v in ("world", "private", "curated", "legacy"):
            assert validate_provenance_kind(v) == v

    def test_out_of_vocabulary_returns_none(self):
        assert validate_provenance_kind("garbage") is None
        assert validate_provenance_kind("maid-private") is None

    def test_none_and_empty_return_none(self):
        assert validate_provenance_kind(None) is None
        assert validate_provenance_kind("") is None
        assert validate_provenance_kind("  ") is None

    def test_normalizes_case_and_whitespace(self):
        assert validate_provenance_kind(" World ") == "world"


class TestStorageColumn:
    def test_atomic_fact_field_defaults_none(self):
        f = AtomicFact()
        assert f.provenance_kind is None

    def test_migration_additive_and_idempotent(self, tmp_path):
        db = _db(tmp_path)
        # M052 已跑过;再跑不炸(additive IF NOT EXISTS)
        M052.run(db)
        M052.run(db)
        cols = [r[1] for r in db.execute("PRAGMA table_info(atomic_facts)")]
        assert "provenance_kind" in cols
        # 存量(如有)为 NULL
        tagged = db.execute(
            "SELECT COUNT(*) AS n FROM atomic_facts "
            "WHERE provenance_kind IS NOT NULL"
        )
        assert tagged[0]["n"] == 0
        db.close()

    def test_migration_adds_column_to_a_store_that_predates_it(
        self, tmp_path: Path,
    ):
        """Upgrade path: a store whose atomic_facts predates M052 gets the column."""
        legacy = tmp_path / "legacy.db"
        with sqlite3.connect(legacy) as conn:
            conn.execute("CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY)")
        with sqlite3.connect(legacy) as conn:
            M052.apply(conn)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)")]
        assert "provenance_kind" in cols


def _seed(db: DatabaseManager) -> None:
    """a:world/global, b:curated/global, c:None/global, d:world/personal."""
    db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    db.store_memory(MemoryRecord(memory_id="m0", profile_id="a", content="parent"))
    for fid, content, kind in (
        ("fa", "a", "world"),
        ("fb", "b", "curated"),
        ("fc", "c", None),
    ):
        db.store_fact(AtomicFact(
            fact_id=fid, memory_id="m0", profile_id="a",
            content=content, scope="global", provenance_kind=kind,
        ))
    db.store_fact(AtomicFact(
        fact_id="fd", memory_id="m0", profile_id="a",
        content="d", scope="personal", provenance_kind="world",
    ))


class TestDBFiltering:
    def test_get_all_facts_scope_filter(self, tmp_path):
        db = _db(tmp_path)
        _seed(db)
        facts = db.get_all_facts("a", scope="global")
        assert {f.content for f in facts} == {"a", "b", "c"}

    def test_get_all_facts_provenance_kind_filter(self, tmp_path):
        db = _db(tmp_path)
        _seed(db)
        facts = db.get_all_facts("a", scope="global", provenance_kind="world")
        assert {f.content for f in facts} == {"a"}
        # The tag survives the row -> AtomicFact round trip.
        assert facts[0].provenance_kind == "world"

    def test_get_all_facts_provenance_null_filter(self, tmp_path):
        db = _db(tmp_path)
        _seed(db)
        facts = db.get_all_facts("a", scope="global", provenance_kind_null=True)
        assert {f.content for f in facts} == {"c"}
        assert facts[0].provenance_kind is None

    def test_update_fact_provenance_kind_in_updatable_columns(self, tmp_path):
        db = _db(tmp_path)
        _seed(db)
        fid = "fb"
        db.update_fact(fid, {"provenance_kind": "curated"}, profile_id="a")
        assert db.get_fact(fid, "a").provenance_kind == "curated"
        # 清标注
        db.update_fact(fid, {"provenance_kind": None}, profile_id="a")
        assert db.get_fact(fid, "a").provenance_kind is None
