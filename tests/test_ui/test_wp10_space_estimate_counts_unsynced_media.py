"""The estimate counts originals the point's own sync will copy into the mirror.

PR 251 round 6, S3: create_point first syncs media/ into the mirror, then copies the
whole mirror. One 8 MB original with no mirror: estimated 1.1 MB, written 17 MB.
An original missing from the mirror (or differing in size) is written twice.
"""

from __future__ import annotations

import hashlib
import sqlite3

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")
SIZE = 3 * 1024 * 1024


def _bytes(folder):
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def _make_root(root):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE marker(v TEXT)")


def _original(base, fill: bytes, size: int = SIZE):
    data = fill * size
    sha = hashlib.sha256(data).hexdigest()
    path = base / sha[:2] / f"{sha}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _estimate_and_actual(root):
    from superlocalmemory.server import backup_bundle, backup_rollback

    needed = backup_rollback.space_needed(root)
    before = _bytes(root / "backups") if (root / "backups").exists() else 0
    backup_bundle.create_point(root)
    copy = backup_rollback.PreRestoreCopy.take(root)
    written = _bytes(root / "backups") - before
    copy.discard()
    return needed, written


def test_empty_mirror_counts_every_original_twice(tmp_path):
    from superlocalmemory.server import backup_rollback

    _make_root(tmp_path)
    (tmp_path / "backups").mkdir()
    _original(tmp_path / "media", b"A")
    _original(tmp_path / "media", b"B")
    needed = backup_rollback.space_needed(tmp_path)
    assert needed >= 4 * SIZE
    needed, written = _estimate_and_actual(tmp_path)
    assert needed >= written, (needed, written)


def test_partial_mirror_counts_only_the_missing_original(tmp_path):
    from superlocalmemory.server import backup_rollback

    _make_root(tmp_path)
    kept = _original(tmp_path / "media", b"A")
    _original(tmp_path / "media", b"B")
    mirror = tmp_path / "backups" / "media-originals" / kept.parent.name
    mirror.mkdir(parents=True)
    (mirror / kept.name).write_bytes(kept.read_bytes())
    needed = backup_rollback.space_needed(tmp_path)
    assert needed >= 3 * SIZE            # mirror once + the missing one twice
    assert needed < 3 * SIZE + 2 * SIZE  # the mirrored one is not counted again
    needed, written = _estimate_and_actual(tmp_path)
    assert needed >= written, (needed, written)


def test_a_mirror_copy_of_a_different_size_is_counted_again(tmp_path):
    from superlocalmemory.server import backup_rollback

    _make_root(tmp_path)
    src = _original(tmp_path / "media", b"A")
    stale = tmp_path / "backups" / "media-originals" / src.parent.name / src.name
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 10)
    assert backup_rollback.space_needed(tmp_path) >= 2 * SIZE


def test_an_up_to_date_mirror_adds_nothing(tmp_path):
    from superlocalmemory.server import backup_rollback

    _make_root(tmp_path)
    src = _original(tmp_path / "media", b"A")
    dest = tmp_path / "backups" / "media-originals" / src.parent.name / src.name
    dest.parent.mkdir(parents=True)
    dest.write_bytes(src.read_bytes())
    with_media = backup_rollback.space_needed(tmp_path)
    src.unlink()  # same mirror, nothing left to sync
    without = backup_rollback.space_needed(tmp_path)
    assert with_media == without
