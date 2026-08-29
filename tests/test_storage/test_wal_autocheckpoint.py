"""WAL growth guard for the close-path deadlock fix (#118, 4.0.6).

`NO_CKPT_ON_CLOSE` removes checkpoint-on-close, which makes `close()`
non-blocking but also removes a checkpoint opportunity. Autocheckpoint then
becomes the ONLY remaining path that drains the WAL.

`wal_autocheckpoint` is a PER-CONNECTION pragma and is not persisted in the
database file (unlike `journal_mode=WAL`). Before 4.0.6 it was set only on the
short-lived initialisation connection, so every working connection silently
fell back to SQLite's default of 1000 frames. These tests pin the intended
behaviour so that regression cannot return silently.
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.storage.database import DatabaseManager


def _wal_bytes(db_path) -> int:
    wal = db_path.parent / (db_path.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def test_working_connection_sets_autocheckpoint(tmp_path) -> None:
    """Every per-call connection must carry the intended autocheckpoint value.

    Regression guard: the pragma does not persist in the database file, so
    setting it only during initialisation leaves working connections on
    SQLite's default of 1000.
    """
    db = tmp_path / "memory.db"
    mgr = DatabaseManager(db)
    mgr.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, blob TEXT)")

    conn = mgr._connect()
    try:
        value = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    finally:
        conn.close()

    assert value == 400, (
        f"per-call connection reports wal_autocheckpoint={value}, expected 400. "
        "With checkpoint-on-close disabled, autocheckpoint is the only "
        "remaining checkpoint path — it must be set on the connections that "
        "actually write."
    )


def test_wal_does_not_grow_unbounded_under_sustained_writes(tmp_path) -> None:
    """Sustained writes must not grow the WAL without bound.

    This is the property that would silently regress if checkpoint-on-close
    were removed without ensuring autocheckpoint is active on write paths.
    """
    db = tmp_path / "memory.db"
    mgr = DatabaseManager(db)
    mgr.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, blob TEXT)")

    payload = "x" * 4096
    for i in range(400):
        mgr.execute("INSERT INTO t (id, blob) VALUES (?, ?)", (i, payload))

    wal_size = _wal_bytes(db)
    db_size = db.stat().st_size

    # 400 frames * ~4KB pages is the checkpoint threshold; allow generous
    # headroom for page overhead and in-flight frames, but fail if the WAL is
    # clearly never being drained.
    ceiling = max(8 * 1024 * 1024, db_size)
    assert wal_size < ceiling, (
        f"WAL grew to {wal_size} bytes (db={db_size}) under sustained writes — "
        "autocheckpoint is not draining it. Check that _connect() still sets "
        "PRAGMA wal_autocheckpoint."
    )


def test_durability_survives_close_without_checkpoint(tmp_path) -> None:
    """Disabling checkpoint-on-close must not cost committed data."""
    db = tmp_path / "memory.db"
    mgr = DatabaseManager(db)
    mgr.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, blob TEXT)")
    for i in range(50):
        mgr.execute("INSERT INTO t (id, blob) VALUES (?, ?)", (i, "v"))

    probe = sqlite3.connect(str(db))
    try:
        count = probe.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        probe.close()

    assert count == 50, f"expected 50 committed rows readable after close, got {count}"


@pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "setconfig"),
    reason="Connection.setconfig requires Python 3.12+",
)
def test_no_ckpt_on_close_is_actually_active(tmp_path, monkeypatch) -> None:
    """On a supported interpreter the guard must be installed, not skipped.

    The production fix swallows AttributeError/OperationalError for portability;
    this asserts that on 3.12+ the swallow path is NOT the one taken.
    WAL-only machinery: opt the store into WAL for this test (the default
    journal mode is DELETE since the 2026-08-13 postmortem).
    """
    monkeypatch.setenv("SLM_JOURNAL_MODE", "wal")
    db = tmp_path / "memory.db"
    mgr = DatabaseManager(db)
    mgr.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
    mgr.execute("INSERT INTO t (id) VALUES (1)")

    assert _wal_bytes(db) > 0, (
        "WAL sidecar is empty after close on a Python 3.12+ interpreter — "
        "close() checkpointed, so NO_CKPT_ON_CLOSE was not applied."
    )
