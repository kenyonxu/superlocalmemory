# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""A plain file copy of everything a restore may change, and putting it back.

The rollback of a dashboard restore must not depend on the code that just
failed (PR 251 re-review N1): re-running a restore against the safety point
repeats a failure that repeats, such as an unreadable media catalog. This copy
is taken with the service stopped and the writer lease held, so the files are
quiescent; putting it back is ``os.replace`` and ``unlink``, nothing more.

Covered: every managed store with its ``-wal``/``-shm`` files, the ``lance/``
vector index folder, the settings files and the mode file, and the set of
content-addressed media originals (files the failed restore added are removed;
``uploads.db`` and other non-original files under ``media/`` are never touched).
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

_SIDECARS = ("", "-wal", "-shm")
#: A restore holds the safety point, this copy and the backup coordinator's own
#: pre-restore snapshot at once: about three times the stores (PR 251 R2).
_COPIES_AT_ONCE = 3
#: Manifests, checksums files and a store that grows while the safety point is
#: taken (an empty media catalog gets its schema): a fixed allowance on top.
_OVERHEAD_BYTES = 1024 * 1024
_PREFIX = ".pre-restore-"


class NotEnoughSpace(RuntimeError):
    """The disk cannot hold what a restore needs; nothing was changed."""


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def space_needed(root: Path) -> int:
    """Every byte a restore's copies write, before it stops anything.

    Three copies of the stores and the vector index exist at once (the
    safety point, the pre-write copy, the coordinator's own snapshot). The
    safety point also copies the picture and document originals mirror
    (PR 251 S3), and both the safety point and the pre-write copy hold the
    settings files. Before that copy, the point's own sync writes every media
    original missing from the mirror into it (PR 251 round 6, S3), so those
    are counted twice: once for the sync, once for the point's copy of the
    mirror. A fixed allowance covers manifests and small growth.
    """
    from superlocalmemory.infra import backup_media
    from superlocalmemory.infra.backup import MANAGED_DATABASES

    root = Path(root)
    stores = sum(_tree_bytes(root / (s + x)) for s in MANAGED_DATABASES for x in _SIDECARS)
    settings = sum(_tree_bytes(root / name) for name in _settings_names())
    originals = (_tree_bytes(root / "backups" / backup_media.MIRROR_DIR)
                 + 2 * backup_media.pending_bytes(root, root / "backups"))
    return (_COPIES_AT_ONCE * (stores + _tree_bytes(root / "lance"))
            + originals + 2 * settings + _OVERHEAD_BYTES)


def check_free_space(root: Path) -> None:
    """Refuse, in plain words, when the disk cannot hold a restore's copies."""
    needed, free = space_needed(root), shutil.disk_usage(Path(root)).free
    if free < needed:
        mb = 1024 * 1024
        raise NotEnoughSpace(
            f"A restore needs about {needed // mb + 1} MB free on this disk and only "
            f"{free // mb} MB are available, so nothing was changed. Free some space and "
            "retry.")


def sweep_stale(root: Path) -> None:
    """Remove pre-restore copies left by an interrupted run, except kept ones."""
    from superlocalmemory.server import recovery_marker

    kept = {Path(c).name for c in recovery_marker.kept_copies(recovery_marker.read(root))}
    backups = Path(root) / "backups"
    if not backups.is_dir():
        return
    for folder in backups.glob(_PREFIX + "*"):
        if folder.name not in kept and folder.is_dir() and not folder.is_symlink():
            shutil.rmtree(folder, ignore_errors=True)


def _settings_names() -> tuple[str, ...]:
    from superlocalmemory.core.config import CURRENT_MODE_FILE
    from superlocalmemory.server.backup_bundle import SETTINGS

    return (*SETTINGS, CURRENT_MODE_FILE)


def _originals(root: Path) -> set[Path]:
    from superlocalmemory.media import files

    base = Path(root) / "media"
    if not base.is_dir():
        return set()
    return {p for p in base.rglob("*") if p.is_file() and not p.is_symlink()
            and files.is_original(p.relative_to(base).as_posix())}


class PreRestoreCopy:
    """Take with :meth:`take`; then :meth:`roll_back` or :meth:`discard`."""

    def __init__(self, root: Path, folder: Path) -> None:
        self.root, self.folder = Path(root), Path(folder)
        self.present: set[str] = set()
        self.originals: set[Path] = set()

    @classmethod
    def take(cls, root: Path) -> PreRestoreCopy:
        from superlocalmemory.infra.backup import MANAGED_DATABASES

        root = Path(root)
        folder = root / "backups" / f"{_PREFIX}{time.time_ns()}"
        folder.mkdir(parents=True, mode=0o700)
        copy = cls(root, folder)
        try:
            copy._fill(MANAGED_DATABASES)
        except BaseException:
            copy.discard()  # a partial copy must never be left to fill the disk
            raise
        return copy

    def _fill(self, stores) -> None:
        names = [s + x for s in stores for x in _SIDECARS] + list(_settings_names())
        for name in names:
            if (self.root / name).is_file():
                shutil.copy2(self.root / name, self.folder / name)
                self.present.add(name)
        if (self.root / "lance").is_dir():
            shutil.copytree(self.root / "lance", self.folder / "lance", symlinks=True)
            self.present.add("lance")
        self.originals = _originals(self.root)

    def roll_back(self) -> None:
        """Put every covered file back as it was. Settings first: they are smallest."""
        from superlocalmemory.infra.backup import MANAGED_DATABASES

        for name in _settings_names():
            self._restore_file(name)
        for store in MANAGED_DATABASES:
            for suffix in _SIDECARS:
                self._restore_file(store + suffix)
        self._restore_lance()
        for added in _originals(self.root) - self.originals:
            added.unlink(missing_ok=True)

    def _restore_file(self, name: str) -> None:
        live = self.root / name
        if name not in self.present:
            live.unlink(missing_ok=True)
            return
        staged = live.with_name(live.name + ".rollback")
        shutil.copy2(self.folder / name, staged)
        os.replace(staged, live)

    def _restore_lance(self) -> None:
        live = self.root / "lance"
        if live.exists():
            shutil.rmtree(live)
        if "lance" in self.present:
            shutil.copytree(self.folder / "lance", live, symlinks=True)

    def discard(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)
