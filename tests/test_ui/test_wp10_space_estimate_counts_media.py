"""The free-space estimate counts every byte a restore's copies write.

PR 251 round 5, S3: the estimate covered the stores and the vector index but
not the picture and document originals that the safety point copies. One
20 MB original: estimated 0.12 MB, written 21 MB, so the check passed and the
safety point could fill the disk after the service had been stopped.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def _bytes(folder):
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def test_the_estimate_covers_the_safety_point_and_the_pre_write_copy(tmp_path):
    from superlocalmemory.server import backup_bundle, backup_rollback

    for name in STORES:
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE marker(v TEXT)")
    (tmp_path / "config.json").write_text(json.dumps({"mode": "a", "note": "x" * 50_000}))
    data = b"M" * (4 * 1024 * 1024)
    sha = hashlib.sha256(data).hexdigest()
    for base in (tmp_path / "backups" / "media-originals", tmp_path / "media"):
        original = base / sha[:2] / f"{sha}.png"
        original.parent.mkdir(parents=True)
        original.write_bytes(data)

    needed = backup_rollback.space_needed(tmp_path)
    before = _bytes(tmp_path / "backups")
    backup_bundle.create_point(tmp_path)
    copy = backup_rollback.PreRestoreCopy.take(tmp_path)
    written = _bytes(tmp_path / "backups") - before
    copy.discard()

    assert needed >= written, (needed, written)
    assert needed >= len(data)
