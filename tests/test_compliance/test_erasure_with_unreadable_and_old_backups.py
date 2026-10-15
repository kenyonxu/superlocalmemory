# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""An erasure with awkward backups around it says so, and the ledger keeps the obligation."""

from __future__ import annotations

import os
import time

import pytest

from superlocalmemory.compliance.gdpr import GDPRCompliance
from superlocalmemory.infra.backup_obligations import BackupObligationStore
from tests.conftest import force_sync_enrichment
from tests.test_compliance.erasure_universe import (
    OTHER,
    PROFILE,
    Universe,
    _seed_backup,
    _seed_engine,
)


@pytest.fixture
def store(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    for pid in (PROFILE, OTHER):
        engine._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (pid, pid))
    u = Universe(engine=engine, root=engine._db.db_path.parent)
    _seed_engine(u)
    return u


def _erase(u):
    return GDPRCompliance(u.engine._db, engine=u.engine, data_root=u.root).forget_profile(PROFILE)


def test_an_unreadable_backup_blocks_completeness_and_is_recorded(store) -> None:
    backups = store.root / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "memory-20260101-000000.db").write_bytes(b"\x00not sqlite\x00" * 300)

    result = _erase(store)

    assert result["backup_snapshots_unreadable"] == 1
    assert result["erasure_complete"] == 0, "an unreadable copy cannot be shown not to hold her"
    [row] = BackupObligationStore(store.root).list_pending_for_profile(PROFILE)
    assert row["kind"] == "unreadable"
    assert result["erasure_report"]["remains"]


def test_a_backup_from_long_ago_is_still_an_obligation_after_the_window(store) -> None:
    _seed_backup(store)
    for snapshot in (store.root / "backups").glob("*.db"):
        old = time.time() - 400 * 86_400
        os.utime(snapshot, (old, old))

    first = _erase(store)
    assert first["backup_obligations_pending"] >= 1

    # Read again after the retention window has long passed: still pending, now overdue.
    ledger = BackupObligationStore(store.root)
    assert ledger.count_pending(PROFILE) >= 1
    assert ledger.list_overdue(PROFILE), "past the window it is reported overdue, not dropped"


def test_deleting_the_backup_is_what_ends_the_obligation(store) -> None:
    _seed_backup(store)
    _erase(store)
    ledger = BackupObligationStore(store.root)
    assert ledger.count_pending(PROFILE) >= 1

    for snapshot in (store.root / "backups").glob("*"):
        if snapshot.is_file():
            snapshot.unlink()

    assert ledger.count_pending(PROFILE) == 0


def test_a_scale_engine_rollback_copy_is_an_obligation_until_it_is_deleted(store) -> None:
    """Graph and vector stores kept for rollback hold entity names and cannot be read here."""
    copy = store.root / "scale-backups" / "promotion-1"
    (copy / "cozo").mkdir(parents=True)
    (copy / "cozo" / "data").write_bytes(b"entity names")

    result = _erase(store)

    assert result["backup_snapshots_unreadable"] == 1 and result["erasure_complete"] == 0
    ledger = BackupObligationStore(store.root)
    assert [r["snapshot_path"] for r in ledger.list_pending_for_profile(PROFILE)] == [str(copy)]
