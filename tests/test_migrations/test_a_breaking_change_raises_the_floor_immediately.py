# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A rebuilt table must not be left guarded by the old version ceiling.

The recorded schema version is a completion certificate: the runner writes it
only when every migration on both databases is complete. That is the right rule
for "is this store fully migrated" and the wrong one for "may an older build
write to it".

The gap: a migration rebuilds `atomic_facts` so the constraint no longer accepts
the value an older build files planned events under. Any unrelated failure —
a deferred migration on the other database — skips the certificate. The ceiling
stays at the old number, an older build's guard passes, it opens the store, and
its first planned event is rejected by the constraint and lost. Which is the
precise outcome the ceiling exists to turn into a refusal to start.

So a migration that breaks older builds declares a floor, and the floor is
written as soon as that migration is complete, whatever else failed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from superlocalmemory.storage import migration_runner as mr
from superlocalmemory.storage._schema_version import (
    SchemaVersionError,
    check_version_or_raise,
    read_schema_version,
)
from superlocalmemory.storage.migrations import (
    M046_prospective_memory_has_its_own_name as M046,
)


def test_the_breaking_migration_declares_a_floor():
    """Without this the runner has nothing to act on."""
    assert getattr(M046, "BREAKING_VERSION", 0) == 46


def _log_complete(db: Path, name: str) -> None:
    """Record a migration as complete, the way the runner would."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS migration_log ("
            " name TEXT PRIMARY KEY, ddl_sha256 TEXT, applied_at TEXT,"
            " status TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO migration_log "
            "(name, ddl_sha256, applied_at, status) "
            "VALUES (?, 'x', datetime('now'), 'complete')",
            (name,),
        )
        conn.commit()
    finally:
        conn.close()


def test_the_floor_is_raised_even_when_something_else_failed(tmp_path):
    """Grok's sequence, driven.

    Only the breaking migration is recorded complete; every other one is
    missing, which is what an unrelated failure looks like to the stamping
    logic. The certificate must be withheld and the floor must still rise.
    """
    learning = tmp_path / "learning.db"
    memory = tmp_path / "memory.db"
    for db in (learning, memory):
        sqlite3.connect(str(db)).close()

    _log_complete(memory, M046.NAME)
    details: dict[str, str] = {}
    mr._stamp_breaking_floor(learning, memory, details)

    assert read_schema_version(memory) >= 46, (
        "a rebuilt store is still reporting a version an older build accepts"
    )


def test_an_older_build_is_then_refused(tmp_path):
    """The property the floor buys, stated as the guard actually behaves."""
    learning = tmp_path / "learning.db"
    memory = tmp_path / "memory.db"
    for db in (learning, memory):
        sqlite3.connect(str(db)).close()
    _log_complete(memory, M046.NAME)
    mr._stamp_breaking_floor(learning, memory, {})

    from superlocalmemory.storage import _schema_version as sv

    original = sv.SUPPORTED_SCHEMA_VERSION
    sv.SUPPORTED_SCHEMA_VERSION = 42  # every build from 4.0.5 to 4.0.10
    try:
        with pytest.raises(SchemaVersionError):
            sv.check_version_or_raise(memory)
    finally:
        sv.SUPPORTED_SCHEMA_VERSION = original

    # And this build still opens what it produced.
    check_version_or_raise(memory)


def test_a_migration_that_did_not_complete_raises_nothing(tmp_path):
    """A failed rebuild changed nothing an older build would trip over.

    Stamping on intent rather than completion would lock users out of a store
    that is still perfectly readable by the build they have.
    """
    learning = tmp_path / "learning.db"
    memory = tmp_path / "memory.db"
    for db in (learning, memory):
        sqlite3.connect(str(db)).close()

    mr._stamp_breaking_floor(learning, memory, {})

    assert read_schema_version(memory) == 0
    assert read_schema_version(learning) == 0


def test_the_floor_never_lowers_a_version(tmp_path):
    """It is a floor. A store already ahead of it must stay there."""
    learning = tmp_path / "learning.db"
    memory = tmp_path / "memory.db"
    for db in (learning, memory):
        sqlite3.connect(str(db)).close()
    _log_complete(memory, M046.NAME)

    from superlocalmemory.storage._schema_version import (
        ensure_schema_version_table,
        write_schema_version,
    )

    # isolation_level=None, matching how the runner connects: these helpers do
    # not commit, so a transactional connection would roll the write back on
    # close and the assertion below would be testing nothing.
    conn = sqlite3.connect(str(memory), isolation_level=None)
    try:
        ensure_schema_version_table(conn)
        write_schema_version(conn, 99)
    finally:
        conn.close()
    assert read_schema_version(memory) == 99, "test setup did not persist"

    mr._stamp_breaking_floor(learning, memory, {})
    assert read_schema_version(memory) == 99, "the floor lowered a stored version"
