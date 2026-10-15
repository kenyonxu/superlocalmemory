# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The maintenance plan re-read classifies wording without holding the write lock.

M048 runs 210 s after every daemon start and every maintenance cycle on the
running store, taking the process write lock per batch. The wording classifier
ran inside that hold: on a copy of a 27,689-fact store (14 Oct 2026) one batch
held the lock 4.72 s while an accepted remember and the event log waited. Only
the guarded UPDATE needs the lock; it re-checks the row, so a write in between
is never clobbered.
"""

from __future__ import annotations

import contextlib
import sqlite3

from superlocalmemory.storage.migrations import (
    M048_upcoming_holds_only_what_is_upcoming as M048,
)
from superlocalmemory.storage.write_lock import get_write_lock

_DDL = (
    "CREATE TABLE atomic_facts (fact_id TEXT PRIMARY KEY, content TEXT, "
    "fact_type TEXT NOT NULL DEFAULT 'semantic' CHECK (fact_type IN "
    "('episodic','semantic','opinion','prospective')))"
)


def test_wording_is_classified_while_the_write_lock_is_free(tmp_path, monkeypatch) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute(_DDL)
        conn.executemany(
            "INSERT INTO atomic_facts (fact_id, content, fact_type) VALUES (?, ?, 'prospective')",
            [(f"f{i}", "We shipped the fix yesterday", ) for i in range(1_200)])
    lock = get_write_lock(path)

    @contextlib.contextmanager
    def opener():  # what DatabaseManager.raw_connection does
        with lock:
            conn = sqlite3.connect(path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    held_while_classifying = []
    real = M048._resolve

    def resolve(content):
        held_while_classifying.append(lock._is_owned())
        return real(content)

    monkeypatch.setattr(M048, "_resolve", resolve)
    M048.apply(open_connection=opener)

    assert len(held_while_classifying) == 1_200
    assert not any(held_while_classifying)
    with sqlite3.connect(path) as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM atomic_facts WHERE fact_type='prospective'").fetchone()[0]
    assert left == 0


def test_reading_the_batches_does_not_take_the_write_lock(tmp_path) -> None:
    """Finding plans reads the table in rowid order; with few plans that is the
    whole table. Only a batch with something to move may take the lock."""
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute(_DDL)
        conn.executemany(
            "INSERT INTO atomic_facts (fact_id, content, fact_type) VALUES (?, ?, 'prospective')",
            [(f"f{i}", "We will ship the fix next Tuesday") for i in range(1_200)])
    lock = get_write_lock(path)
    opened = []

    @contextlib.contextmanager
    def opener():
        opened.append(1)
        with lock:
            conn = sqlite3.connect(path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    M048.apply(open_connection=opener)
    assert len(opened) == 1  # the schema probe; every plan stayed a plan


def test_a_row_changed_between_read_and_write_is_not_overwritten(tmp_path, monkeypatch) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute(_DDL)
        conn.execute("INSERT INTO atomic_facts VALUES ('f', 'We shipped the fix yesterday', "
                     "'prospective')")
    lock = get_write_lock(path)

    @contextlib.contextmanager
    def opener():
        with lock:
            conn = sqlite3.connect(path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    real = M048._resolve

    def resolve(content):
        with sqlite3.connect(path) as other:  # a person re-files it meanwhile
            other.execute("UPDATE atomic_facts SET fact_type='opinion' WHERE fact_id='f'")
        return real(content)

    monkeypatch.setattr(M048, "_resolve", resolve)
    M048.apply(open_connection=opener)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT fact_type FROM atomic_facts").fetchone()[0] == "opinion"
