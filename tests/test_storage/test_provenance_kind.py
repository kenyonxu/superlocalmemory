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


class TestMaterializerSurvival:
    """Task-2 handoff regression: the queryable→final promotion keeps the tag.

    The daemon materializer promotes the already-queryable receipt in place
    through ``store_pipeline``'s fixed-column UPDATE. That UPDATE deliberately
    omits the governance columns (scope / shared_with / provenance_kind): an
    ``UPDATE SET`` only touches listed columns, so the values written at
    submit survive the promotion. These tests pin that property — a future
    full-row rewrite of the promotion would silently null every tag on every
    daemon-materialized fact, and must fail here instead.
    """

    def _row(self, engine, fact_id: str) -> dict:
        rows = engine._db.execute(
            "SELECT provenance_kind, scope, shared_with, "
            "canonical_entities_json, embedding "
            "FROM atomic_facts WHERE fact_id = ?",
            (fact_id,),
        )
        assert rows, "promoted fact row must exist"
        return dict(rows[0])

    def test_daemon_materializer_promotion_keeps_tag_and_scope(
        self, engine_with_mock_deps,
    ):
        """Queryable write → forced materializer pass → governance columns intact.

        Mirrors the daemon's two-phase shape exactly: ``require_complete=False``
        commits the queryable receipt (tag set), then the background
        materializer's own entry point (``IngestionCommand.materialize``) runs
        the promotion.
        """
        from superlocalmemory.core.engine_ingestion import (
            build_engine_ingestion_command,
            canonical_store,
            local_trusted_actor_id,
        )
        from superlocalmemory.core.ingestion_command import IngestionState

        engine = engine_with_mock_deps
        receipt = canonical_store(
            engine,
            "Aurelia keeps the lighthouse ledger for the northern reef",
            source_type="python-api",
            trusted_actor_id=local_trusted_actor_id("python-api"),
            scope="shared",
            shared_with=("harbormaster",),
            provenance_kind="world",
            require_complete=False,
            return_receipt=True,
        )
        fact_id = receipt.fact_ids[0]

        # The queryable phase already carries the tag (submit wrote it).
        before = self._row(engine, fact_id)
        assert before["provenance_kind"] == "world"
        assert before["scope"] == "shared"

        # The exact promotion pass the daemon worker runs on a queryable
        # receipt (the fixed-column UPDATE in store_pipeline).
        command = build_engine_ingestion_command(engine)
        result = command.materialize(receipt.operation_id)
        assert result.state is IngestionState.COMPLETE

        after = self._row(engine, fact_id)
        # The promotion really ran: the enrichment columns materialized
        # (the queryable stub is written without them).
        assert after["embedding"] not in (None, b"", "")
        assert after["canonical_entities_json"] not in (None, "", "[]")
        # Governance columns survive the fixed-column promotion UPDATE.
        assert after["provenance_kind"] == "world"
        assert after["scope"] == "shared"
        assert "harbormaster" in (after["shared_with"] or "")

    def test_require_complete_promotion_keeps_tag(self, engine_with_mock_deps):
        """``require_complete=True`` drives submit+materialize in one call —
        the synchronous contract the Python API exposes. Same survival
        property, asserted straight off the durable row."""
        from superlocalmemory.core.engine_ingestion import (
            canonical_store,
            local_trusted_actor_id,
        )
        from tests.conftest import force_sync_enrichment

        engine = force_sync_enrichment(engine_with_mock_deps)
        fact_ids = engine.store(
            "Bramwell charts the tide windows for the southern approach",
            provenance_kind="curated",
        )
        assert fact_ids, "synchronous store must produce a fact"
        row = self._row(engine, fact_ids[0])
        assert row["embedding"] not in (None, b"", "")
        assert row["provenance_kind"] == "curated"
        assert row["scope"] == "personal"


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
