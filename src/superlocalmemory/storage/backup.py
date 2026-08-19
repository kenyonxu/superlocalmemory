# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Pre-migration database backup utilities.

Provides a consistent, WAL-safe snapshot of both managed databases before
any schema migration runs. Uses the SQLite backup API rather than a
filesystem copy so that in-flight writers on a live WAL database cannot
produce a torn snapshot.

Snapshots are written as flat files directly in ``snapshots_root``
(default: ``canonical_data_root() / "pre-migration-snapshots"``).
This directory is separate from the ``backups/`` directory managed by
``BackupManager``, so pre-migration snapshots are never subject to
``BackupManager._enforce_retention()`` regardless of how many ordinary
backups accumulate.  The separation is structural — not timing-dependent.

Public API intended for use by the migration runner:
  - _backup_via_sqlite_api(src, dest)
  - _pre_migration_backup(learning_db, memory_db, *, backups_root) -> Path
  - _gc_old_backups(backups_root, keep=2) -> None
  - InsufficientDiskSpaceError

Restoring a pre-migration snapshot
-----------------------------------
When a migration goes wrong and you need to roll back to the database state
captured immediately before the migration ran, use ``BackupManager`` pointed
at the snapshots directory.  Example (adapt paths to your installation)::

    from pathlib import Path
    from superlocalmemory.infra.backup import BackupManager
    from superlocalmemory.infra.data_root import canonical_data_root

    snapshots_dir = canonical_data_root() / "pre-migration-snapshots"
    mgr = BackupManager(
        base_dir=canonical_data_root(),
        backup_dir=snapshots_dir,
    )

    # List snapshot files to find the one taken before the failed migration:
    #   ls -lt ~/.superlocalmemory/pre-migration-snapshots/
    # Then pass the bare filename (no directory component):
    mgr.restore_backup("memory-20260819-120000-pre-migration.db")

``restore_backup`` derives the target database from the filename stem
(``"memory"`` → ``memory.db``), creates a safety snapshot of the current
state first, and overwrites the live database via ``sqlite3.backup()``.

Why snapshots live outside ``backups/``
----------------------------------------
``BackupManager._enforce_retention()`` globs only its own ``backup_dir``
(``canonical_data_root() / "backups"``).  Because pre-migration snapshots
are in ``pre-migration-snapshots/`` — a completely different directory — no
retention policy can delete them, regardless of how many ordinary backups
accumulate.  An ordinary ``BackupManager()`` call (without ``backup_dir``
override) will not list or touch these files.  That is intentional.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from superlocalmemory.infra.data_root import canonical_data_root

logger = logging.getLogger(__name__)


class InsufficientDiskSpaceError(Exception):
    """Raised when the filesystem cannot hold the pre-migration backup.

    Attributes:
        needed_bytes: How many bytes would be required.
        free_bytes: How many bytes are currently available.
    """

    def __init__(self, needed_bytes: int, free_bytes: int) -> None:
        self.needed_bytes = needed_bytes
        self.free_bytes = free_bytes
        super().__init__(
            f"Insufficient disk space for pre-migration backup: "
            f"need {needed_bytes:,} bytes, have {free_bytes:,} bytes free"
        )


def _backup_via_sqlite_api(src: Path, dest: Path) -> None:
    """Copy a live SQLite database to dest using the SQLite backup API.

    Unlike a filesystem copy, sqlite3.Connection.backup() acquires page-level
    read locks one batch at a time, allowing concurrent writers to proceed
    between batches. The resulting snapshot reflects only committed pages —
    uncommitted WAL frames are never included.

    dest.parent is created if it does not exist.
    Both connections are closed in a finally block even if an error occurs.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src), check_same_thread=False)
    dst_conn = sqlite3.connect(str(dest))
    try:
        # pages=-1 copies all pages in a single pass (fastest; no yielding to
        # other writers between batches, which is acceptable here because the
        # backup happens before the migration run begins — no other migration
        # writer is active at this point).
        src_conn.backup(dst_conn, pages=-1)
    finally:
        src_conn.close()
        dst_conn.close()


def _find_existing_ancestor(path: Path) -> Path:
    """Return the nearest ancestor of path that exists on the filesystem."""
    p = path
    while not p.exists():
        if p.parent == p:
            # Reached the root without finding an existing dir; use cwd.
            return Path.cwd()
        p = p.parent
    return p


def _pre_migration_backup(
    learning_db: Path,
    memory_db: Path,
    *,
    backups_root: Path | None = None,
) -> Path:
    """Snapshot both databases as flat files before migration.

    Creates ``{db_stem}-{YYYYMMDD-HHmmss}-pre-migration.db`` files directly
    in ``backups_root`` (no subdirectory). Files are named with a
    ``-pre-migration`` suffix so that the GC glob ``*-pre-migration.db``
    identifies them unambiguously without matching any file produced by
    ``BackupManager``.

    Only databases that exist on disk are copied; a missing database is
    silently skipped (first-install scenario where learning.db may not
    exist yet).

    The ``backups_root`` directory defaults to
    ``canonical_data_root() / "pre-migration-snapshots"`` — a directory
    separate from ``BackupManager``'s ``backups/`` directory.  This
    separation means ``BackupManager._enforce_retention()`` can never
    reach these files regardless of how many routine backups accumulate.

    Args:
        learning_db: Path to the learning-plane database.
        memory_db: Path to the memory database.
        backups_root: Override the canonical snapshots root.  Tests pass a
            tmp_path here to avoid writing to the user's data directory.

    Returns:
        The Path of ``backups_root`` (the directory holding the new flat
        snapshot files).

    Raises:
        InsufficientDiskSpaceError: When the free space on the target
            filesystem is less than 110% of the combined source database sizes.
    """
    if backups_root is None:
        backups_root = canonical_data_root() / "pre-migration-snapshots"

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    # Measure combined size of databases that actually exist.
    total_bytes = 0
    for db_path in (memory_db, learning_db):
        if db_path.exists():
            total_bytes += db_path.stat().st_size

    # Check that the target filesystem has enough room.  We check against an
    # existing ancestor because the snapshot directory itself may not exist yet.
    check_path = _find_existing_ancestor(backups_root)
    free_bytes = shutil.disk_usage(str(check_path)).free
    needed_bytes = int(total_bytes * 1.1)
    if free_bytes < needed_bytes:
        raise InsufficientDiskSpaceError(needed_bytes, free_bytes)

    backups_root.mkdir(parents=True, exist_ok=True)

    # Perform the backup.  Each db gets a flat file with a -pre-migration suffix
    # so the GC glob *-pre-migration.db identifies our files precisely.
    t0 = time.monotonic()
    for stem, db_path in (("memory", memory_db), ("learning", learning_db)):
        if db_path.exists():
            dest = backups_root / f"{stem}-{timestamp}-pre-migration.db"
            _backup_via_sqlite_api(db_path, dest)

    elapsed = time.monotonic() - t0

    written = sorted(backups_root.glob(f"*-{timestamp}-pre-migration.db"))
    size_bytes = sum(f.stat().st_size for f in written)
    size_mb = size_bytes / (1024 * 1024)
    filenames = "\n  ".join(f.name for f in written)

    logger.info(
        "[SLM] Pre-migration snapshot written (%.0f MB in %.1fs):\n"
        "  Location : %s\n"
        "  Files    :\n  %s\n"
        "  To restore if migration fails:\n"
        "    from superlocalmemory.infra.backup import BackupManager\n"
        "    from superlocalmemory.infra.data_root import canonical_data_root\n"
        "    mgr = BackupManager(\n"
        "        base_dir=canonical_data_root(),\n"
        "        backup_dir=%r,\n"
        "    )\n"
        "    mgr.restore_backup(%r)",
        size_mb,
        elapsed,
        str(backups_root),
        filenames,
        str(backups_root),
        written[0].name if written else "<snapshot file>",
    )

    return backups_root


def _gc_old_backups(backups_root: Path, keep: int = 2) -> None:
    """Remove oldest pre-migration snapshot files, retaining ``keep`` newest.

    Only flat files directly under ``backups_root`` whose names match the
    ``*-pre-migration.db`` pattern are eligible for deletion. Files without
    this suffix (such as ``memory-*.db`` or ``learning-*.db`` produced by
    ``BackupManager``) are never touched. Every deletion uses an explicit
    full path after confirming the path's parent is ``backups_root`` — no
    glob pattern is passed to the deletion call.

    Args:
        backups_root: The directory that holds pre-migration snapshot files.
        keep: Number of most-recent snapshots to retain.  Defaults to 2.
    """
    if not backups_root.exists():
        return

    # List only direct children matching *-pre-migration.db that are files.
    candidates: list[Path] = [
        f
        for f in backups_root.glob("*-pre-migration.db")
        if f.is_file() and f.parent == backups_root
    ]

    if len(candidates) <= keep:
        return

    # Sort ascending by mtime so oldest entries are first.
    candidates.sort(key=lambda f: f.stat().st_mtime)

    to_delete = candidates[: len(candidates) - keep]
    for target in to_delete:
        # Double-check the invariant before touching anything.
        if target.parent != backups_root:
            logger.warning(
                "[SLM] GC skipped %s — parent is not backups_root", target
            )
            continue
        logger.info("[SLM] GC removing old snapshot: %s", target)
        target.unlink()
