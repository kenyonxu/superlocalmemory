# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A backup that still holds an erased person keeps its erasure obligation (OPS-06, OPS-07).

OPS-06: ``backup_obligations`` discharged every obligation 90 days after the
snapshot was taken, on a ``SELECT``, without removing or checking the snapshot,
so a retained backup fell out of the restore-replay ledger while it still held
the person. OPS-07: a snapshot that could not be read was classified as clean.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from superlocalmemory.infra.backup_obligations import (
    BackupObligationStore,
    SnapshotVerdict,
    inspect_snapshot,
    scan_backup_snapshots,
)

PERSON = "alice"
DAY = 86_400


def _snapshot(path: Path, *, profile: str | None = PERSON, table: str = "memories") -> Path:
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {table} (id TEXT, profile_id TEXT, content TEXT)")
    if profile is not None:
        conn.execute(f"INSERT INTO {table} VALUES ('m1', ?, 'words')", (profile,))
    conn.commit()
    conn.close()
    return path


def _old(path: Path, days: int) -> None:
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))


def test_a_retained_backup_stays_an_obligation_past_the_retention_window(tmp_path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    snap = _snapshot(backups / "memory-20260101-000000.db")
    _old(snap, 400)  # far past 90 days
    store = BackupObligationStore(tmp_path)
    store.record(profile_id=PERSON, erasure_id="e1", snapshot_path=str(snap),
                 snapshot_epoch=int(snap.stat().st_mtime), retention_days=90)

    assert store.count_pending(PERSON) == 1, "age discharged an obligation the file still backs"
    assert [r["snapshot_path"] for r in store.list_pending_for_profile(PERSON)] == [str(snap)]
    [overdue] = store.list_overdue(PERSON)
    assert overdue["snapshot_path"] == str(snap), "past the window it is OVERDUE, not gone"


def test_an_obligation_ends_when_the_snapshot_is_removed(tmp_path) -> None:
    snap = _snapshot(tmp_path / "memory-1.db")
    store = BackupObligationStore(tmp_path)
    store.record(profile_id=PERSON, erasure_id="e1", snapshot_path=str(snap), snapshot_epoch=1)
    assert store.count_pending(PERSON) == 1

    snap.unlink()

    assert store.count_pending(PERSON) == 0
    assert store.list_pending_for_profile(PERSON) == []


def test_reconcile_discharges_only_what_it_proves_clean_or_gone(tmp_path) -> None:
    holding = _snapshot(tmp_path / "holding.db")
    clean = _snapshot(tmp_path / "clean.db", profile=None)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all" * 100)
    store = BackupObligationStore(tmp_path)
    for snap in (holding, clean, broken):
        store.record(profile_id=PERSON, erasure_id="e1", snapshot_path=str(snap), snapshot_epoch=1)

    result = store.reconcile(PERSON)

    assert result == {"discharged": 1, "still_holding": 1, "unreadable": 1}
    assert {Path(r["snapshot_path"]).name for r in store.list_pending_for_profile(PERSON)} == {
        "holding.db", "broken.db"}


def test_a_restore_replay_does_not_end_the_obligation(tmp_path) -> None:
    """The live store is clean after a replay; the snapshot still holds the person."""
    snap = _snapshot(tmp_path / "memory-1.db")
    store = BackupObligationStore(tmp_path)
    store.record(profile_id=PERSON, erasure_id="e1", snapshot_path=str(snap), snapshot_epoch=1)

    assert store.mark_replayed(str(snap)) == 1

    assert store.count_pending(PERSON) == 1


# ----------------------------------------------------------------------- OPS-07

def test_a_corrupt_snapshot_is_unknown_not_clean(tmp_path) -> None:
    bad = tmp_path / "memory-corrupt.db"
    bad.write_bytes(b"\x00corrupt" * 200)

    assert inspect_snapshot(bad, PERSON) is SnapshotVerdict.UNKNOWN


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_an_unreadable_snapshot_is_unknown_not_clean(tmp_path) -> None:
    snap = _snapshot(tmp_path / "memory-locked.db")
    snap.chmod(0)
    try:
        assert inspect_snapshot(snap, PERSON) is SnapshotVerdict.UNKNOWN
    finally:
        snap.chmod(0o600)


def test_the_scan_reports_unreadable_snapshots_beside_the_ones_that_hold_the_person(
        tmp_path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    _snapshot(backups / "memory-1.db")
    _snapshot(backups / "memory-2.db", profile="someone-else")
    (backups / "memory-3.db").write_bytes(b"\x01" * 500)

    report = scan_backup_snapshots(backups, PERSON)

    assert [Path(p).name for p, _ in report.hits] == ["memory-1.db"]
    assert [Path(p).name for p, _ in report.unreadable] == ["memory-3.db"]


def test_every_table_with_a_profile_is_looked_at_not_a_short_list(tmp_path) -> None:
    """A backup of learning.db or pending.db holds the person's words as much as memory.db."""
    pending = _snapshot(tmp_path / "pending-1.db", table="pending_memories")
    learning = _snapshot(tmp_path / "learning-1.db", table="learning_signals")
    proof_only = _snapshot(tmp_path / "audit-1.db", table="erasure_receipts")

    assert inspect_snapshot(pending, PERSON) is SnapshotVerdict.CONTAINS
    assert inspect_snapshot(learning, PERSON) is SnapshotVerdict.CONTAINS
    assert inspect_snapshot(proof_only, PERSON) is SnapshotVerdict.CLEAN, (
        "the receipt that proves an erasure is not the person's data")


def test_an_old_ledger_gains_the_new_columns(tmp_path) -> None:
    """A ledger written by an earlier release opens without losing a row."""
    conn = sqlite3.connect(tmp_path / "backup_obligations.db")
    conn.executescript(
        "CREATE TABLE backup_obligations (obligation_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,"
        " erasure_id TEXT NOT NULL, snapshot_path TEXT NOT NULL, snapshot_epoch INTEGER NOT NULL,"
        " retention_days INTEGER NOT NULL DEFAULT 90, recorded_at REAL NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending', discharged_at REAL, discharge_reason TEXT);"
        "INSERT INTO backup_obligations VALUES ('o1','alice','e0','/nowhere/x.db',1,90,1.0,"
        "'pending',NULL,NULL);")
    conn.commit()
    conn.close()

    store = BackupObligationStore(tmp_path)  # upgrades in place

    cols = {r[1] for r in sqlite3.connect(tmp_path / "backup_obligations.db").execute(
        "PRAGMA table_info(backup_obligations)")}
    assert {"kind", "last_replayed_at"} <= cols
    assert store.count_pending(PERSON) == 0, "its snapshot is gone, so it ends on evidence"
