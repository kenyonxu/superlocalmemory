# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Restoring a backup does not bring back a memory that was erased on request (OPS-33).

A profile erasure left an obligation outside every backup; a targeted one (one
memory, one person's mentions) did not, so a restore of a backup taken before it
returned the erased facts, active and recalled, with no obligation recorded.
"""

from __future__ import annotations

import sqlite3

import pytest
from tests.conftest import force_sync_enrichment
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.core.engine_ingestion import canonical_store, local_trusted_actor_id
from superlocalmemory.infra import erased_subjects
from superlocalmemory.infra.backup import BackupManager
from superlocalmemory.retrieval import vector_physical

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)

PROFILE = "default"
ERASED = "Mirabel Quenby keeps the sealed ZQXRESTORE-4417 ledger in the Tallinn vault."
KEPT = "The Tuesday deploy window is unchanged for the whole quarter."


def _store(engine, text: str) -> list[str]:
    receipt = canonical_store(
        engine, text, source_type="python-api",
        trusted_actor_id=local_trusted_actor_id("python-api"), require_complete=True,
        return_receipt=True, profile_id=PROFILE)
    return list(receipt.final_fact_ids)


@pytest.fixture
def world(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    erased, kept = _store(engine, ERASED), _store(engine, KEPT)
    root = engine._db.db_path.parent
    engine._db.execute("PRAGMA wal_checkpoint(FULL)")
    manager = BackupManager(base_dir=root)
    name = manager.create_backup(label="before-erasure")
    assert name
    return engine, root, manager, name, erased, kept


def _facts(db_path, ids):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            f"SELECT fact_id FROM atomic_facts WHERE fact_id IN ({','.join('?' * len(ids))})", ids)}
    finally:
        conn.close()


def _text_locations(db_path, needle: str) -> dict[str, int]:
    """Every table.column of the store whose text holds the needle."""
    conn = sqlite3.connect(db_path)
    found: dict[str, int] = {}
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            try:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            except sqlite3.Error:
                continue
            for col in cols:
                try:
                    n = conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" '
                        f'WHERE instr(CAST("{col}" AS TEXT), ?) > 0', (needle,)).fetchone()[0]
                except sqlite3.Error:
                    continue
                if n:
                    found[f"{table}.{col}"] = n
    finally:
        conn.close()
    return found


def test_a_restore_does_not_resurrect_a_targeted_erasure(world) -> None:
    engine, root, manager, name, erased, kept = world
    result = GDPRCompliance(engine._db, engine=engine, data_root=root).forget_facts(
        erased, PROFILE, subject_id="doc-1")
    assert not _facts(root / "memory.db", erased), "premise: the facts are erased"

    assert manager.restore_backup(name) is True

    live = _facts(root / "memory.db", erased + kept)
    assert not (live & set(erased)), "the restore brought the erased facts back"
    assert set(kept) <= live, "the rest of the profile survives the replay"
    conn = sqlite3.connect(root / "memory.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE content LIKE '%ZQXRESTORE%'"
                            ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ingestion_operations WHERE raw_content "
                            "LIKE '%ZQXRESTORE%'").fetchone()[0] == 0
    finally:
        conn.close()
    assert not _text_locations(root / "memory.db", "ZQXRESTORE"), \
        _text_locations(root / "memory.db", "ZQXRESTORE")
    assert b"ZQXRESTORE" not in (root / "memory.db").read_bytes()
    index = vector_physical.open_vec(root / "memory.db")
    try:
        assert not vector_physical.indexed_fact_ids(index) & set(erased)
    finally:
        index.close()
    assert {s.target_id for s in erased_subjects.list_subjects(root)} >= set(erased)
    assert result is not None


def test_the_erasure_is_not_complete_while_a_backup_still_holds_the_memory(world) -> None:
    engine, root, _, _, erased, _ = world

    result = GDPRCompliance(engine._db, engine=engine, data_root=root).forget_facts(
        erased, PROFILE, subject_id="doc-1")

    assert result["backup_obligations_pending"] >= 1
    assert result["erasure_complete"] == 0


def test_a_journal_that_cannot_be_written_is_a_failure_not_silence(world, monkeypatch) -> None:
    engine, root, _, _, erased, _ = world

    def _no(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(erased_subjects, "record", _no)

    result = GDPRCompliance(engine._db, engine=engine, data_root=root).forget_facts(
        erased, PROFILE, subject_id="doc-1")

    assert result["restore_journal_failed"] == 1 and result["erasure_complete"] == 0


def test_a_restore_that_cannot_replay_is_reported_not_clean(world, monkeypatch) -> None:
    engine, root, manager, name, erased, _ = world
    GDPRCompliance(engine._db, engine=engine, data_root=root).forget_facts(
        erased, PROFILE, subject_id="doc-1")

    def _boom(*_a, **_k):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(erased_subjects, "replay_into", _boom)

    assert manager.restore_backup(name) is False


def test_an_entity_erasure_is_replayed_too(world) -> None:
    engine, root, manager, name, erased, kept = world
    conn = sqlite3.connect(root / "memory.db")
    conn.execute("INSERT OR IGNORE INTO canonical_entities (entity_id, profile_id, "
                 "canonical_name, entity_type) VALUES ('e-mirabel', ?, 'Mirabel Quenby', 'person')",
                 (PROFILE,))
    conn.commit()
    conn.close()
    erased_subjects.record(root, PROFILE, "entity", ["e-mirabel"], "e-1")
    name = manager.create_backup(label="has-entity")
    conn = sqlite3.connect(root / "memory.db")
    conn.execute("DELETE FROM canonical_entities WHERE entity_id = 'e-mirabel'")
    conn.commit()
    conn.close()

    assert manager.restore_backup(name) is True

    conn = sqlite3.connect(root / "memory.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM canonical_entities WHERE entity_id = 'e-mirabel'"
                            ).fetchone()[0] == 0
    finally:
        conn.close()
