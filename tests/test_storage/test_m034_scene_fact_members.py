# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""M034 scene membership projection migration tests."""

from __future__ import annotations

import sqlite3

from superlocalmemory.storage.migrations import M034_scene_fact_members as migration


def test_migration_backfills_only_live_profile_scoped_members() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE profiles (profile_id TEXT PRIMARY KEY);
        INSERT INTO profiles VALUES ('default');
        CREATE TABLE atomic_facts (
            fact_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL
        );
        INSERT INTO atomic_facts VALUES ('live-1', 'default');
        INSERT INTO atomic_facts VALUES ('live-2', 'default');
        CREATE TABLE memory_scenes (
            scene_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO memory_scenes VALUES (
            'scene-1', 'default', '["live-1", "deleted", "live-2"]'
        );
        """
    )

    conn.executescript(migration.DDL)

    assert migration.verify(conn) is True
    rows = conn.execute(
        "SELECT fact_id, position FROM scene_fact_members ORDER BY position"
    ).fetchall()
    assert rows == [("live-1", 0), ("live-2", 2)]


def test_projection_trigger_replaces_members_atomically() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE profiles (profile_id TEXT PRIMARY KEY);
        INSERT INTO profiles VALUES ('default');
        CREATE TABLE atomic_facts (
            fact_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL
        );
        INSERT INTO atomic_facts VALUES ('first', 'default');
        INSERT INTO atomic_facts VALUES ('second', 'default');
        CREATE TABLE memory_scenes (
            scene_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    conn.executescript(migration.DDL)

    conn.execute(
        "INSERT INTO memory_scenes VALUES (?, ?, ?)",
        ("scene-1", "default", '["first"]'),
    )
    conn.execute(
        "UPDATE memory_scenes SET fact_ids_json = ? WHERE scene_id = ?",
        ('["second"]', "scene-1"),
    )

    assert conn.execute(
        "SELECT fact_id FROM scene_fact_members"
    ).fetchall() == [("second",)]
