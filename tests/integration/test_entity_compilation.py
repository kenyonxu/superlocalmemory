# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Integration tests for Entity Compilation Engine (Phase D)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture
def entity_db(tmp_path):
    """Create a temp DB with required tables + sample entities."""
    db_path = tmp_path / "entity_test.db"
    conn = sqlite3.connect(str(db_path))

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS atomic_facts (
            fact_id TEXT PRIMARY KEY, memory_id TEXT DEFAULT '',
            content TEXT, confidence REAL DEFAULT 0.8,
            created_at TEXT, profile_id TEXT DEFAULT 'default',
            canonical_entities_json TEXT DEFAULT '[]',
            fact_type TEXT DEFAULT 'fact'
        );
        CREATE TABLE IF NOT EXISTS canonical_entities (
            entity_id TEXT PRIMARY KEY, profile_id TEXT DEFAULT 'default',
            canonical_name TEXT, entity_type TEXT DEFAULT 'person',
            first_seen TEXT, last_seen TEXT, fact_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fact_importance (
            fact_id TEXT PRIMARY KEY, profile_id TEXT,
            pagerank_score REAL, community_id INTEGER,
            degree_centrality REAL, computed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS entity_profiles (
            profile_entry_id TEXT PRIMARY KEY,
            entity_id TEXT, profile_id TEXT DEFAULT 'default',
            knowledge_summary TEXT DEFAULT '', fact_ids_json TEXT DEFAULT '[]',
            last_updated TEXT DEFAULT '',
            project_name TEXT DEFAULT '',
            compiled_truth TEXT DEFAULT '',
            timeline TEXT DEFAULT '[]',
            compilation_confidence REAL DEFAULT 0.5,
            last_compiled_at TEXT DEFAULT NULL
        );
    """)

    # Insert sample entity
    entity_id = "ent-alice-001"
    conn.execute(
        "INSERT INTO canonical_entities VALUES (?, 'default', 'Alice', 'person', ?, ?, 5)",
        (entity_id, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
    )

    # Insert 5 facts about Alice
    for i in range(5):
        fid = f"fact-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, content, confidence, created_at, profile_id, canonical_entities_json) "
            "VALUES (?, ?, ?, ?, 'default', ?)",
            (fid, f"Alice fact {i}: she works on AI project {i} at Qualixar",
             0.8 + i * 0.02, datetime.now(timezone.utc).isoformat(),
             json.dumps([entity_id])),
        )

    conn.commit()
    conn.close()
    return db_path, entity_id


class TestEntityCompiler:

    def test_compile_all_finds_entities(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))
        result = compiler.compile_all("default")
        assert result["compiled"] >= 1

    def test_compiled_truth_under_2000_chars(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))
        compiler.compile_all("default")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT compiled_truth FROM entity_profiles WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert len(row[0]) <= 2000
        assert len(row[0]) > 0

    def test_compiled_truth_contains_entity_name(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))
        compiler.compile_all("default")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT compiled_truth FROM entity_profiles WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        conn.close()
        assert "Alice" in row[0]

    def test_timeline_appended(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))

        # Compile twice
        compiler.compile_all("default")
        # Reset last_compiled_at to force recompilation
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE entity_profiles SET last_compiled_at='1970-01-01'")
        conn.commit()
        conn.close()
        compiler.compile_all("default")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT timeline FROM entity_profiles WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        conn.close()

        timeline = json.loads(row[0])
        assert len(timeline) >= 2  # Two compilations

    def test_fact_ids_stored(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))
        compiler.compile_all("default")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT fact_ids_json FROM entity_profiles WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        conn.close()

        fact_ids = json.loads(row[0])
        assert len(fact_ids) >= 1

    def test_profile_isolation(self, entity_db):
        """Facts in profile 'work' should not appear in profile 'personal'."""
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))

        # Compile default profile
        compiler.compile_all("default")

        # Compile 'personal' profile — no entities there
        result = compiler.compile_all("personal")
        assert result["compiled"] == 0

    def test_skip_when_disabled(self, entity_db):
        db_path, entity_id = entity_db

        class MockConfig:
            entity_compilation_enabled = False
            mode = type('obj', (object,), {'value': 'a'})()

        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path), config=MockConfig())
        result = compiler.compile_all("default")
        assert result["reason"] == "disabled"

    def test_compile_single_entity(self, entity_db):
        db_path, entity_id = entity_db
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        compiler = EntityCompiler(str(db_path))
        result = compiler.compile_entity("default", "", entity_id, "Alice")
        assert result is not None
        assert result["entity_name"] == "Alice"
        assert result["facts_used"] >= 1

    def test_truncate_at_sentence_boundary(self):
        from superlocalmemory.learning.entity_compiler import EntityCompiler
        text = "First sentence. Second sentence. Third sentence. " * 50
        truncated = EntityCompiler._truncate(text, 100)
        assert len(truncated) <= 100
        assert truncated.endswith(".")
