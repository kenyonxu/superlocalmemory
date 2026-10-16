# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Listing retention rules is a read: it does not wait for the database writer."""

import sqlite3
import threading

from superlocalmemory.compliance.retention import RetentionEngine
from superlocalmemory.storage.write_lock import get_write_lock


def test_peek_returns_rules_from_a_read_only_connection(tmp_path):
    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    engine = RetentionEngine(conn)
    engine.add_rule("default", "keep-90", 90)
    conn.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rules = RetentionEngine.peek_rules(ro, "default")
    ro.close()
    assert [r["rule_name"] for r in rules] == ["keep-90"]


def test_peek_says_so_when_only_a_writer_can_answer(tmp_path):
    """An older rules table needs the writer to migrate it before it can be read."""
    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE retention_rules (rule_id INTEGER PRIMARY KEY, profile_id TEXT)")
    conn.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert RetentionEngine.peek_rules(ro, "default") is None
    ro.close()


def test_no_rules_table_yet_reads_as_no_rules_without_creating_one(tmp_path):
    path = tmp_path / "memory.db"
    sqlite3.connect(path).close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert RetentionEngine.peek_rules(ro, "default") == []
    ro.close()
    tables = sqlite3.connect(path).execute("SELECT name FROM sqlite_master").fetchall()
    assert tables == []


def test_the_route_helper_does_not_wait_for_the_write_lock(tmp_path, monkeypatch):
    from superlocalmemory.server.routes import compliance

    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    RetentionEngine(conn).add_rule("default", "keep-90", 90)
    conn.close()
    monkeypatch.setattr(compliance, "DB_PATH", path)
    held, release, result = threading.Event(), threading.Event(), []

    def hold():
        with get_write_lock(path):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    reader = threading.Thread(target=lambda: result.append(compliance._retention_rules("default")))
    reader.start()
    reader.join(3)
    answered = not reader.is_alive()
    release.set()
    holder.join(5)
    reader.join(5)
    assert answered and [r["rule_name"] for r in result[0]] == ["keep-90"]
