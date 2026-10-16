# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Erasing a profile that has real write receipts (OPS-11) and what the receipt then says (UX-31).

Every save the daemon accepts leaves a row in ``write_commits``, which database
triggers forbid deleting. A test store built by inserting facts directly has none,
which is why the generic sweep's collision with the trigger was never seen: every
normal erase of a real profile failed on it, after the profile's facts were gone.
These tests write through the real runtime, so the receipts are the real ones.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.storage.write_receipts_erasure import (
    ReceiptErasureError,
    erase_profile_receipts,
)
from tests.conftest import force_sync_enrichment
from tests.test_compliance.erasure_universe import OTHER, PROFILE, Universe, _seed_receipts


@pytest.fixture
def real_store(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    for pid in (PROFILE, OTHER):
        engine._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (pid, pid))
    u = Universe(engine=engine, root=engine._db.db_path.parent)
    _seed_receipts(u)
    return u


def _receipts(db_path, profile):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM write_commits WHERE profile_id = ?", (profile,)).fetchone()[0]
    finally:
        conn.close()


def test_the_real_runtime_leaves_receipts_for_the_profile(real_store):
    assert _receipts(real_store.engine._db.db_path, PROFILE) >= 1


def test_a_profile_with_real_receipts_erases_completely(real_store):
    db = real_store.engine._db
    out = GDPRCompliance(db, engine=real_store.engine, data_root=real_store.root).forget_profile(
        PROFILE)

    assert not out.get("table_delete_failures"), "the sweep still collides with the trigger"
    assert out["residue_rows"] == 0
    assert out["write_receipts"] >= 1
    assert out["erasure_complete"] == 1
    assert _receipts(db.db_path, PROFILE) == 0


def test_the_ledger_stays_protected_for_everyone_else(real_store):
    """The erasure lifts the trigger for one transaction; afterwards it is back."""
    db_path = real_store.engine._db.db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO write_commits(command_id, journal_id, command_kind, request_hash, "
        "profile_id, idempotency_key, operation_id, receipt_json, committed_at) "
        "VALUES ('c-keep','j-keep','admission','h',?, 'k-keep','op-keep','{}',1.0)", (OTHER,))
    conn.commit()
    conn.close()

    erase_profile_receipts(db_path, PROFILE)

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM write_commits WHERE profile_id = ?", (OTHER,)).fetchone()[0] == 1
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute("DELETE FROM write_commits WHERE profile_id = ?", (OTHER,))
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute("UPDATE write_commits SET operation_id = 'x'")
    finally:
        conn.close()


def test_a_failed_receipt_erasure_rolls_the_protection_back(real_store, monkeypatch):
    db_path = real_store.engine._db.db_path
    import superlocalmemory.storage.write_receipts_erasure as module

    real = module._delete_with_triggers_lifted

    def _explode(conn, profile_id):
        conn.execute("DELETE FROM write_commits WHERE profile_id = ?", (profile_id,)).rowcount
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(module, "_delete_with_triggers_lifted", _explode)
    with pytest.raises(ReceiptErasureError):
        erase_profile_receipts(db_path, PROFILE)
    monkeypatch.setattr(module, "_delete_with_triggers_lifted", real)

    conn = sqlite3.connect(str(db_path))
    try:
        # Nothing was lost and the triggers are exactly as they were.
        assert conn.execute("SELECT COUNT(*) FROM write_commits WHERE profile_id = ?",
                            (PROFILE,)).fetchone()[0] >= 1
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='write_commits'")}
        assert len(names) == 2
    finally:
        conn.close()


def _stored_receipt(db, profile):
    with db.raw_connection() as conn:
        row = conn.execute(
            "SELECT state, all_erased, owner_evidence_json FROM erasure_receipts "
            "WHERE profile_id = ? AND subject_type = 'profile'", (profile,)).fetchone()
    return row


def test_a_complete_erasure_stores_a_complete_receipt(real_store):
    db = real_store.engine._db
    GDPRCompliance(db, engine=real_store.engine, data_root=real_store.root).forget_profile(PROFILE)

    state, all_erased, _ = _stored_receipt(db, PROFILE)
    assert (state, all_erased) == ("COMPLETE", 1)


def test_a_failed_step_is_in_the_stored_receipt_not_only_in_the_response(
        real_store, monkeypatch):
    """The owner receipt used to be written first and say COMPLETE whatever followed."""
    import superlocalmemory.storage.write_receipts_erasure as module

    def _refuse(*_a, **_k):
        raise ReceiptErasureError("receipts are immutable here")

    monkeypatch.setattr(module, "erase_profile_receipts", _refuse)
    db = real_store.engine._db
    out = GDPRCompliance(db, engine=real_store.engine, data_root=real_store.root).forget_profile(
        PROFILE)

    assert out["write_receipts_failed"] == 1
    assert out["erasure_complete"] == 0 and out["erasure_provable"] == 0
    state, all_erased, evidence = _stored_receipt(db, PROFILE)
    assert (state, all_erased) == ("FAILED", 0), "the stored receipt must not say COMPLETE"
    proofs = {p["owner"]: p for p in json.loads(evidence)["proofs"]}
    assert proofs["profile_erasure"]["erased"] is False
    assert "write_receipts_failed" in proofs["profile_erasure"]["residue"]
