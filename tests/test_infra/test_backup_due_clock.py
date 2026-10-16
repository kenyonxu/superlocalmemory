# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A backup that was just taken is not due again (OPS-34).

``create_backup`` saves an aware UTC time; ``is_backup_due`` compared it with a
naive local ``datetime.now()``, which raises, and the handler read the raised
error as "overdue". Every scheduler check then took another full backup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from superlocalmemory.infra.backup import BackupManager


@pytest.fixture
def manager(tmp_path):
    (tmp_path / "memory.db").write_bytes(b"")
    m = BackupManager(base_dir=tmp_path)
    m.config.update({"enabled": True, "interval_hours": 24})
    return m


def _saved(manager, when: datetime | str) -> None:
    manager.config["last_backup"] = when if isinstance(when, str) else when.isoformat()


def test_a_backup_made_now_is_not_due(manager) -> None:
    _saved(manager, datetime.now(timezone.utc))
    assert manager.is_backup_due() is False
    assert manager.get_status()["next_backup"] not in ("overdue", "unknown")


def test_it_becomes_due_past_the_interval(manager) -> None:
    _saved(manager, datetime.now(timezone.utc) - timedelta(hours=25))
    assert manager.is_backup_due() is True
    assert manager.get_status()["next_backup"] == "overdue"


def test_the_same_instant_in_another_zone_gives_the_same_answer(manager) -> None:
    instant = datetime.now(timezone.utc) - timedelta(hours=1)
    plus_five = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    _saved(manager, instant)
    first = manager.is_backup_due()
    _saved(manager, plus_five)
    assert manager.is_backup_due() is first is False


def test_an_older_naive_value_is_read_as_local_time(manager) -> None:
    _saved(manager, datetime.now().replace(microsecond=0))  # naive, local, just now
    assert manager.is_backup_due() is False
    _saved(manager, datetime.now() - timedelta(hours=30))
    assert manager.is_backup_due() is True


def test_a_malformed_value_is_due_and_logged(manager, caplog) -> None:
    _saved(manager, "yesterday-ish")
    with caplog.at_level("WARNING"):
        assert manager.is_backup_due() is True
    assert any("not a date" in r.message for r in caplog.records)
    assert manager.get_status()["next_backup"] == "unknown"


def test_check_and_backup_takes_one_backup_not_one_per_check(tmp_path) -> None:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    m = BackupManager(base_dir=tmp_path)
    m.config.update({"enabled": True, "interval_hours": 24, "last_backup": None})

    first = m.check_and_backup()
    second = m.check_and_backup()

    assert first and second is None
    assert len([b for b in m.list_backups() if b.get("type") == "memory"]) == 1
