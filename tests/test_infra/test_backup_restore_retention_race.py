"""Retention must never be able to destroy the backup being restored.

Regression guard for a reproduced data-loss path in
``BackupManager.restore_backup()``:

    1. ``restore_backup`` confirms the source file exists.
    2. It then calls ``create_backup(label="pre-restore")``, which runs
       ``_enforce_retention()`` over the same directory.
    3. Retention unlinks the oldest files. When the backup being restored IS
       the oldest, it is deleted.
    4. ``sqlite3.connect`` on that now-missing path RECREATES it as an empty
       database, which is copied over the live store.
    5. The call returns ``True``, and leaves a zero-byte file under the
       original name, so a second attempt also appears to succeed.

Measured before the fix: 11 backups with ``max_backups=10``, a 500-fact store
restored to 0 tables, ``restore_backup()`` returning ``True``.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from superlocalmemory.infra.backup import BackupManager


def _seed(path: Path, n: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE atomic_facts(fact_id TEXT, content TEXT)")
    conn.executemany("INSERT INTO atomic_facts VALUES(?,?)",
                     [(f"f{i}", f"m{i}") for i in range(n)])
    conn.commit()
    conn.close()


def _facts(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM atomic_facts").fetchone()[0]
    finally:
        conn.close()


def _copy(src: Path, dest: Path) -> None:
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(str(dest))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def test_restore_survives_retention_deleting_the_oldest_backup(tmp_path):
    live = tmp_path / "memory.db"
    _seed(live, 500)
    backups = tmp_path / "backups"
    backups.mkdir()

    # The backup we will restore is the OLDEST, so retention targets it first.
    oldest = backups / "memory-20260819-000000.db"
    _copy(live, oldest)
    for i in range(1, 11):
        time.sleep(0.01)
        _copy(live, backups / f"memory-20260819-1200{i:02d}.db")
    assert len(list(backups.glob("memory-*.db"))) == 11

    # Live store diverges, as after a bad migration.
    conn = sqlite3.connect(live)
    conn.execute("DELETE FROM atomic_facts WHERE rowid > 100")
    conn.commit()
    conn.close()
    assert _facts(live) == 100

    mgr = BackupManager(db_path=live, base_dir=tmp_path, backup_dir=backups)
    assert mgr.restore_backup(oldest.name) is True

    assert _facts(live) == 500, (
        "the live store must hold the restored content; 0 tables here means "
        "retention deleted the source and an empty database was copied over it"
    )


def test_restore_refuses_a_tableless_backup_rather_than_emptying_the_store(tmp_path):
    live = tmp_path / "memory.db"
    _seed(live, 42)
    backups = tmp_path / "backups"
    backups.mkdir()

    # Exactly what sqlite3.connect() leaves behind after an unlink.
    hollow = backups / "memory-20260819-000000.db"
    sqlite3.connect(hollow).close()

    mgr = BackupManager(db_path=live, base_dir=tmp_path, backup_dir=backups)
    assert mgr.restore_backup(hollow.name) is False, (
        "a backup with no tables must be refused, not copied over the live store"
    )
    assert _facts(live) == 42, "the live store must be untouched on refusal"
