"""The safety copy a failed restore points at is never pruned.

PR 251 re-review N2: ``slm backup restore-safety-copy`` takes a point before it
restores, and taking a point prunes to ``max_backups``. With the outcome
already "failed" nothing was protected, so with retention 1 the command
deleted the very copy it was about to restore.
"""

from __future__ import annotations

import json
import sqlite3

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


def seed(root, value="before"):
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS marker(value TEXT)")
            conn.execute("DELETE FROM marker")
            conn.execute("INSERT INTO marker VALUES(?)", (value,))


def markers(root):
    out = set()
    for name in STORES:
        with sqlite3.connect(root / name) as conn:
            out.add(conn.execute("SELECT value FROM marker").fetchone()[0])
    return out


def kept(root, point_id):
    return (root / "backups" / f"backup_{point_id}").is_dir()


def test_the_cli_restores_its_safety_copy_with_retention_one(tmp_path):
    from superlocalmemory.server import backup_bundle
    from superlocalmemory.server.backup_restore_txn import recover_safety_copy

    seed(tmp_path)
    (tmp_path / "backup_config.json").write_text(json.dumps({"max_backups": 1}))
    safety = backup_bundle.create_point(tmp_path)["id"]
    seed(tmp_path, "after")
    (tmp_path / "backup-recovery-outcome.json").write_text(
        json.dumps({"state": "failed", "safety_id": safety}))

    result = recover_safety_copy(tmp_path, safety)

    assert result["restored"] == safety
    assert markers(tmp_path) == {"before"}


def test_a_failed_restore_safety_copy_survives_later_points(tmp_path):
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    (tmp_path / "backup_config.json").write_text(json.dumps({"max_backups": 1}))
    safety = backup_bundle.create_point(tmp_path)["id"]
    (tmp_path / "backup-recovery-outcome.json").write_text(
        json.dumps({"state": "failed", "safety_id": safety, "recovery_needed": True}))
    for _ in range(3):
        backup_bundle.create_point(tmp_path)
    assert kept(tmp_path, safety)


def test_after_a_clean_outcome_the_old_safety_copy_follows_retention(tmp_path):
    from superlocalmemory.server import backup_bundle

    seed(tmp_path)
    (tmp_path / "backup_config.json").write_text(json.dumps({"max_backups": 1}))
    safety = backup_bundle.create_point(tmp_path)["id"]
    (tmp_path / "backup-recovery-outcome.json").write_text(
        json.dumps({"state": "complete", "safety_id": safety}))
    backup_bundle.create_point(tmp_path)
    assert not kept(tmp_path, safety)
