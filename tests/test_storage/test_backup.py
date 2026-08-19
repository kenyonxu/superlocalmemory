# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Tests for the pre-migration database backup module.

All tests use scratch directories under tmp_path. They never touch
~/.superlocalmemory/memory.db or ~/Downloads/memory.db.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest

from superlocalmemory.storage.backup import (
    InsufficientDiskSpaceError,
    _backup_via_sqlite_api,
    _gc_old_backups,
    _pre_migration_backup,
)

import shutil


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wal_db(path: Path) -> None:
    """Create a minimal WAL-mode SQLite database with one committed row."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def _make_simple_db(path: Path) -> None:
    """Create a minimal SQLite database (default journal mode)."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (99)")
    conn.commit()
    conn.close()


def _integrity_ok(path: Path) -> bool:
    """Return True if PRAGMA integrity_check returns 'ok'."""
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _backup_via_sqlite_api
# ---------------------------------------------------------------------------


class TestBackupViaSqliteApi:
    def test_creates_dest_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_simple_db(src)
        dest = tmp_path / "sub" / "dest.db"

        _backup_via_sqlite_api(src, dest)

        assert dest.exists(), "Backup file must be created"

    def test_dest_parent_created_automatically(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_simple_db(src)
        dest = tmp_path / "deep" / "nested" / "dest.db"

        _backup_via_sqlite_api(src, dest)

        assert dest.parent.exists()

    def test_backup_is_valid_sqlite(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_simple_db(src)
        dest = tmp_path / "dest.db"

        _backup_via_sqlite_api(src, dest)

        assert _integrity_ok(dest), "PRAGMA integrity_check must return 'ok'"

    def test_backup_contains_committed_data(self, tmp_path: Path) -> None:
        src = tmp_path / "src.db"
        _make_simple_db(src)
        dest = tmp_path / "dest.db"

        _backup_via_sqlite_api(src, dest)

        conn = sqlite3.connect(str(dest))
        rows = conn.execute("SELECT v FROM t").fetchall()
        conn.close()
        assert rows == [(99,)]

    def test_backup_consistent_under_live_writer(self, tmp_path: Path) -> None:
        """Gate test: backup of WAL database with an uncommitted transaction.

        The SQLite backup API reads committed pages only. An in-flight write
        that has not been committed must not appear in the snapshot.
        """
        src = tmp_path / "memory.db"

        # Bootstrap a WAL database.
        conn1 = sqlite3.connect(str(src))
        conn1.execute("PRAGMA journal_mode=WAL")
        conn1.execute("CREATE TABLE t (v INTEGER)")
        conn1.commit()

        # Start an uncommitted transaction with data in the WAL.
        conn1.execute("BEGIN")
        conn1.execute("INSERT INTO t VALUES (42)")
        # conn1 not yet committed — WAL has a pending page

        dest = tmp_path / "backup" / "memory.db"
        _backup_via_sqlite_api(src, dest)  # must not block; must not include uncommitted data

        conn2 = sqlite3.connect(str(dest))
        rows = conn2.execute("SELECT * FROM t").fetchall()
        conn2.close()

        assert rows == [], (
            "Uncommitted writes must not appear in the backup snapshot"
        )

        conn1.commit()
        conn1.close()

    def test_integrity_check_after_concurrent_writer(self, tmp_path: Path) -> None:
        """Backup taken concurrently with an in-flight writer is byte-consistent."""
        src = tmp_path / "memory.db"

        conn1 = sqlite3.connect(str(src))
        conn1.execute("PRAGMA journal_mode=WAL")
        conn1.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(50):
            conn1.execute("INSERT INTO data VALUES (?, ?)", (i, f"val_{i}"))
        conn1.commit()

        # Begin a write that will be in-flight during backup.
        conn1.execute("BEGIN")
        for i in range(50, 100):
            conn1.execute("INSERT INTO data VALUES (?, ?)", (i, f"val_{i}"))

        dest = tmp_path / "backup" / "memory.db"
        _backup_via_sqlite_api(src, dest)

        assert _integrity_ok(dest), "Backup must pass integrity_check"

        conn1.rollback()
        conn1.close()

    def test_uses_sqlite_backup_api_not_shutil(self, tmp_path: Path) -> None:
        """Verify the implementation uses sqlite3.Connection.backup, not shutil.copy2.

        The SQLite backup API is a C-level method on sqlite3.Connection —
        conn.backup is read-only and cannot be monkeypatched directly. We
        prove the contract two ways:
        1. shutil.copy2 is never called (it must not appear in the code path).
        2. The backup destination is a fully-readable, non-empty SQLite file
           produced by the backup call, which is only possible if backup() ran.
        """
        src = tmp_path / "src.db"
        _make_simple_db(src)
        dest = tmp_path / "dest.db"

        with mock.patch("shutil.copy2") as mock_copy:
            _backup_via_sqlite_api(src, dest)

        # shutil.copy2 must never be invoked — it cannot produce a consistent
        # WAL snapshot.
        mock_copy.assert_not_called()

        # The result must be a valid, readable SQLite database — only the
        # backup API (not a raw byte copy with copy2 blocked) can produce it.
        assert _integrity_ok(dest), "Backup result must pass SQLite integrity_check"

        # Confirm the source module does not contain a shutil.copy2() call.
        # A docstring mention is acceptable; an actual call is not.
        import ast
        import inspect
        import superlocalmemory.storage.backup as backup_module
        source = inspect.getsource(backup_module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Detect shutil.copy2(...) as an attribute call.
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "copy2"
                ):
                    raise AssertionError(
                        "backup.py must not call shutil.copy2() — found a call at "
                        f"line {func.end_lineno}"
                    )


# ---------------------------------------------------------------------------
# _pre_migration_backup
# ---------------------------------------------------------------------------


class TestPreMigrationBackup:
    def test_returns_path(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        result = _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        assert isinstance(result, Path)

    def test_creates_both_db_files(self, tmp_path: Path) -> None:
        """Both databases are backed up as flat files in snapshots_root."""
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        mem_snaps = list(snapshots_root.glob("memory-*-pre-migration.db"))
        lrn_snaps = list(snapshots_root.glob("learning-*-pre-migration.db"))
        assert len(mem_snaps) == 1, "Exactly one memory snapshot file must be created"
        assert len(lrn_snaps) == 1, "Exactly one learning snapshot file must be created"

    def test_snapshot_files_are_flat_in_snapshots_root(self, tmp_path: Path) -> None:
        """Snapshot files sit directly in snapshots_root — no subdirectory."""
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        for f in snapshots_root.glob("*-pre-migration.db"):
            assert f.parent == snapshots_root, (
                f"Snapshot file {f.name} must be directly in snapshots_root, not in a subdir"
            )

    def test_both_backups_pass_integrity_check(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_wal_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        for snap in snapshots_root.glob("*-pre-migration.db"):
            assert _integrity_ok(snap), f"{snap.name} backup must pass integrity_check"

    def test_raises_insufficient_disk_space(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        # Simulate 0 bytes free.
        mock_usage = shutil.disk_usage(str(tmp_path))._replace(free=0)
        with mock.patch("shutil.disk_usage", return_value=mock_usage):
            with pytest.raises(InsufficientDiskSpaceError):
                _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

    def test_no_backup_created_when_disk_full(self, tmp_path: Path) -> None:
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        mock_usage = shutil.disk_usage(str(tmp_path))._replace(free=0)
        with mock.patch("shutil.disk_usage", return_value=mock_usage):
            with pytest.raises(InsufficientDiskSpaceError):
                _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        # No snapshot files should have been created.
        snaps = list(snapshots_root.glob("*-pre-migration.db")) if snapshots_root.exists() else []
        assert snaps == [], "No snapshot files should be created when disk space check fails"

    def test_skips_missing_db(self, tmp_path: Path) -> None:
        """If one db does not exist, backup only the one that does."""
        mem = tmp_path / "memory.db"
        _make_simple_db(mem)
        lrn = tmp_path / "learning.db"  # intentionally absent
        snapshots_root = tmp_path / "snapshots"

        _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        mem_snaps = list(snapshots_root.glob("memory-*-pre-migration.db"))
        lrn_snaps = list(snapshots_root.glob("learning-*-pre-migration.db"))
        assert len(mem_snaps) == 1, "memory snapshot must be created"
        assert len(lrn_snaps) == 0, "learning snapshot must not be created when db is absent"

    def test_returns_snapshots_root(self, tmp_path: Path) -> None:
        """_pre_migration_backup returns the snapshots root directory itself."""
        mem = tmp_path / "memory.db"
        lrn = tmp_path / "learning.db"
        _make_simple_db(mem)
        _make_simple_db(lrn)
        snapshots_root = tmp_path / "snapshots"

        result = _pre_migration_backup(lrn, mem, backups_root=snapshots_root)

        assert result == snapshots_root, (
            "Return value must be the snapshots root, not a child subdirectory"
        )


# ---------------------------------------------------------------------------
# _gc_old_backups
# ---------------------------------------------------------------------------


class TestGcOldBackups:
    def _make_snapshot_files(
        self, root: Path, names: list[str], mtime_offsets: list[float]
    ) -> list[Path]:
        """Create synthetic pre-migration snapshot files with controlled mtimes.

        Each file is named ``{name}-pre-migration.db`` and is a valid (empty)
        SQLite database so that stat() calls succeed.
        """
        root.mkdir(parents=True, exist_ok=True)
        files = []
        for name, offset in zip(names, mtime_offsets):
            f = root / f"{name}-pre-migration.db"
            # Touch as a valid SQLite file.
            conn = sqlite3.connect(str(f))
            conn.close()
            t = time.time() - offset  # offset seconds in the past
            os.utime(str(f), (t, t))
            files.append(f)
        return files

    def test_keeps_two_newest(self, tmp_path: Path) -> None:
        root = tmp_path / "snapshots"
        names = [f"memory-2026010{i}-120000" for i in range(5)]
        # offsets: 400s, 300s, 200s, 100s, 10s ago → oldest first in the list
        files = self._make_snapshot_files(root, names, [400, 300, 200, 100, 10])

        _gc_old_backups(root, keep=2)

        remaining = sorted(f.name for f in root.glob("*-pre-migration.db"))
        expected = sorted([f"{names[3]}-pre-migration.db", f"{names[4]}-pre-migration.db"])
        assert remaining == expected, (
            f"Expected two newest to remain, got: {remaining}"
        )

    def test_no_op_when_fewer_than_keep(self, tmp_path: Path) -> None:
        root = tmp_path / "snapshots"
        names = ["memory-20260101-120000", "memory-20260102-120000"]
        self._make_snapshot_files(root, names, [200, 100])

        _gc_old_backups(root, keep=2)

        remaining = sorted(f.name for f in root.glob("*-pre-migration.db"))
        expected = sorted(f"{n}-pre-migration.db" for n in names)
        assert remaining == expected, "No files should be removed when count <= keep"

    def test_no_op_when_root_absent(self, tmp_path: Path) -> None:
        root = tmp_path / "does_not_exist"
        # Must not raise.
        _gc_old_backups(root, keep=2)

    def test_never_removes_non_pre_migration_file(self, tmp_path: Path) -> None:
        """GC safety: files without the -pre-migration.db suffix are never touched."""
        root = tmp_path / "snapshots"
        root.mkdir()

        # Ordinary-named files that must never be removed.
        safe_file = root / "memory-20260819-120000.db"
        _make_simple_db(safe_file)
        other_file = root / "some_other_data.db"
        _make_simple_db(other_file)

        # Add enough pre-migration files to trigger GC.
        names = [f"memory-2026010{i}-120000" for i in range(5)]
        self._make_snapshot_files(root, names, [400, 300, 200, 100, 10])

        _gc_old_backups(root, keep=2)

        assert safe_file.exists(), "memory-*.db file without -pre-migration suffix must not be removed"
        assert other_file.exists(), "Unrelated .db file must not be removed by GC"

    def test_deleted_paths_are_children_of_snapshots_root(self, tmp_path: Path) -> None:
        """Every deleted file must have snapshots_root as its direct parent."""
        root = tmp_path / "snapshots"
        names = [f"memory-2026010{i}-120000" for i in range(5)]
        self._make_snapshot_files(root, names, [400, 300, 200, 100, 10])

        deleted: list[Path] = []
        original_unlink = Path.unlink

        def recording_unlink(self_path, missing_ok=False):
            deleted.append(self_path)
            original_unlink(self_path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", recording_unlink):
            _gc_old_backups(root, keep=2)

        assert len(deleted) == 3, f"Expected 3 deletions, got {len(deleted)}"
        for p in deleted:
            assert p.parent == root, (
                f"Deleted path {p} must have snapshots_root as parent"
            )

    def test_exactly_keep_count_remains_after_multiple_runs(self, tmp_path: Path) -> None:
        root = tmp_path / "snapshots"
        names = [f"memory-2026010{i}-120000" for i in range(10)]
        self._make_snapshot_files(root, names, list(range(1000, 0, -100)))

        _gc_old_backups(root, keep=2)
        remaining = list(root.glob("*-pre-migration.db"))
        assert len(remaining) == 2

        # Running again must be idempotent.
        _gc_old_backups(root, keep=2)
        remaining_after = list(root.glob("*-pre-migration.db"))
        assert len(remaining_after) == 2

    def test_custom_keep_value(self, tmp_path: Path) -> None:
        root = tmp_path / "snapshots"
        names = [f"memory-2026010{i}-120000" for i in range(5)]
        self._make_snapshot_files(root, names, [400, 300, 200, 100, 10])

        _gc_old_backups(root, keep=3)

        remaining = list(root.glob("*-pre-migration.db"))
        assert len(remaining) == 3


# ---------------------------------------------------------------------------
# Retention privilege: pre-migration snapshots survive BackupManager churn
# ---------------------------------------------------------------------------


class TestPreMigrationPrivilege:
    def test_pre_migration_backup_survives_backup_manager_retention(
        self, tmp_path: Path
    ) -> None:
        """Privilege test: BackupManager._enforce_retention() cannot reach snapshots.

        Pre-migration snapshots live in ``<data_root>/pre-migration-snapshots/``.
        BackupManager._enforce_retention() globs only ``<data_root>/backups/``.
        These are different directories. Regardless of how many ordinary backups
        accumulate, _enforce_retention() can never delete our snapshot files.
        """
        from superlocalmemory.infra.backup import BackupManager

        data_root = tmp_path / "slm"
        data_root.mkdir()
        snapshots_root = data_root / "pre-migration-snapshots"

        # Source databases.
        mem_src = data_root / "memory.db"
        lrn_src = data_root / "learning.db"
        _make_simple_db(mem_src)
        _make_simple_db(lrn_src)

        # Create the pre-migration snapshot.
        _pre_migration_backup(lrn_src, mem_src, backups_root=snapshots_root)
        snapshot_files_before = set(f.name for f in snapshots_root.glob("*-pre-migration.db"))
        assert snapshot_files_before, "Pre-migration snapshot must be created"

        # Drive BackupManager past its retention limit into data_root/backups/.
        # Use max_backups=2 so retention fires after every third backup.
        mgr = BackupManager(base_dir=data_root)
        mgr.config["max_backups"] = 2
        for _ in range(8):  # 8 >> max_backups of 2; retention fires multiple times
            mgr.create_backup()

        # Snapshots in the separate directory must be completely untouched.
        snapshot_files_after = set(f.name for f in snapshots_root.glob("*-pre-migration.db"))
        assert snapshot_files_after == snapshot_files_before, (
            "BackupManager retention must never delete pre-migration snapshots: "
            "they live in a separate directory (pre-migration-snapshots/) that "
            "_enforce_retention() never globs"
        )

    def test_gc_never_removes_backup_manager_style_files(
        self, tmp_path: Path
    ) -> None:
        """GC safety: *-pre-migration.db glob never matches BackupManager filenames.

        BackupManager writes ``memory-{ts}.db`` and ``learning-{ts}.db``.
        Our GC glob is ``*-pre-migration.db``. Even if both file types land in
        the same root, the glob must only match our files.
        """
        root = tmp_path / "snapshots"
        root.mkdir()

        # BackupManager-style files (no -pre-migration suffix) — must survive.
        ordinary = [
            root / "memory-20260819-120000.db",
            root / "learning-20260819-120000.db",
            root / "memory-20260820-083000.db",
        ]
        for f in ordinary:
            _make_simple_db(f)

        # Pre-migration files — enough to trigger GC (keep=2, create 5).
        for i in range(5):
            f = root / f"memory-2026080{i}-120000-pre-migration.db"
            _make_simple_db(f)

        _gc_old_backups(root, keep=2)

        # BackupManager-style files must be completely untouched.
        for f in ordinary:
            assert f.exists(), (
                f"GC must not remove BackupManager-style file: {f.name}"
            )

        # Only 2 pre-migration files should remain.
        remaining = list(root.glob("*-pre-migration.db"))
        assert len(remaining) == 2, (
            f"Expected 2 pre-migration files to remain after GC, got {len(remaining)}"
        )


# ---------------------------------------------------------------------------
# Deprecation of legacy backup_database in migrations.py
# ---------------------------------------------------------------------------


class TestLegacyBackupDatabaseDeprecated:
    def test_backup_database_emits_deprecation_warning(self, tmp_path: Path) -> None:
        from superlocalmemory.storage.migrations import backup_database

        db = tmp_path / "test.db"
        _make_simple_db(db)

        with pytest.warns(DeprecationWarning):
            backup_database(db)

    def test_backup_database_still_creates_a_file(self, tmp_path: Path) -> None:
        """Legacy function must not crash — dead v1 path depends on it."""
        from superlocalmemory.storage.migrations import backup_database

        db = tmp_path / "test.db"
        _make_simple_db(db)

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = backup_database(db)

        assert result.exists()


# ---------------------------------------------------------------------------
# End-to-end restore: snapshot survives into a usable recovery
# ---------------------------------------------------------------------------


class TestRestoreFromSnapshot:
    def test_restore_from_snapshot_end_to_end(self, tmp_path: Path) -> None:
        """Gate test: snapshot written before migration is restorable after failure.

        Write a known database → snapshot it → simulate a failed migration by
        overwriting the live database with different data → restore from the
        snapshot → assert the original data came back.

        This test exercises the full chain: _pre_migration_backup creates the
        snapshot, BackupManager.restore_backup (with backup_dir pointed at the
        snapshots directory) performs the restore via sqlite3.backup().
        """
        from superlocalmemory.infra.backup import BackupManager

        data_root = tmp_path / "slm"
        data_root.mkdir()
        snapshots_root = data_root / "pre-migration-snapshots"

        # Source database with a known row.
        mem_db = data_root / "memory.db"
        conn = sqlite3.connect(str(mem_db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'original-data')")
        conn.commit()
        conn.close()

        # Snapshot before migration.
        _pre_migration_backup(mem_db, mem_db, backups_root=snapshots_root)

        snaps = list(snapshots_root.glob("memory-*-pre-migration.db"))
        assert len(snaps) == 1, "Snapshot file must be created"
        snap_filename = snaps[0].name

        # Simulate a failed migration: overwrite with a different value.
        conn = sqlite3.connect(str(mem_db))
        conn.execute("DELETE FROM items")
        conn.execute("INSERT INTO items VALUES (1, 'corrupted-after-migration')")
        conn.commit()
        conn.close()

        # Confirm the damage is real before restoring.
        conn = sqlite3.connect(str(mem_db))
        rows = conn.execute("SELECT val FROM items").fetchall()
        conn.close()
        assert rows == [("corrupted-after-migration",)], "Pre-restore state must show the damage"

        # Restore from snapshot: BackupManager pointed at the snapshots dir.
        mgr = BackupManager(base_dir=data_root, backup_dir=snapshots_root)
        ok = mgr.restore_backup(snap_filename)
        assert ok is True, (
            f"restore_backup() must return True for a valid snapshot; returned {ok!r}"
        )

        # Original data must be back.
        conn = sqlite3.connect(str(mem_db))
        rows = conn.execute("SELECT val FROM items").fetchall()
        conn.close()
        assert rows == [("original-data",)], (
            f"Restore must return the pre-migration data; got {rows!r}"
        )
