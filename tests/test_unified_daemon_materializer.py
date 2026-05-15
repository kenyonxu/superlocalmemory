# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""T1: Unit tests for materializer scope/shared_with extraction.

These tests mock the materializer loop's dependencies and verify that
scope and shared_with are correctly extracted from pending metadata
and passed to AtomicFact and the memories table INSERT.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_engine():
    """Return a mock engine with minimal attributes for the materializer."""
    engine = MagicMock()
    engine._profile_id = "test_profile"
    engine._db = MagicMock()
    engine._db.execute.return_value = []
    engine.store_fact_direct = MagicMock()
    return engine


def _run_loop_once(mock_engine, pending_items):
    """Simulate one iteration of the materializer _loop() body.

    This is a direct extraction of the relevant logic from _loop()
    so we can test it without starting threads or importing the full module.
    """
    for item in pending_items:
        content = item["content"]
        import hashlib
        content_hash = hashlib.md5(content.encode()).hexdigest()
        # Dedup check (mock returns empty = no dup)
        dup = mock_engine._db.execute(
            "SELECT 1 FROM atomic_facts WHERE content = ? LIMIT 1",
            (content,),
        )
        if dup:
            continue
        md_str = item.get("metadata") or "{}"
        try:
            md = _json.loads(md_str)
        except Exception:
            md = {}
        if item.get("tags"):
            md.setdefault("tags", item["tags"])
        # T1: extract scope and shared_with from metadata
        scope = md.pop("scope", "personal")
        shared_with_raw = md.pop("shared_with", None)
        shared_with = shared_with_raw if isinstance(shared_with_raw, list) else None
        # Create memory row (FK target for atomic_facts)
        mem_id = content_hash[:16]
        mock_engine._db.execute(
            "INSERT OR IGNORE INTO memories "
            "(memory_id, profile_id, content, "
            "session_id, speaker, role, created_at, "
            "scope, shared_with, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mem_id, mock_engine._profile_id, content,
             "", "", "user",
             datetime.now(timezone.utc).isoformat(),
             scope,
             _json.dumps(shared_with) if shared_with else None,
             _json.dumps(md)),
        )
        from superlocalmemory.storage.models import AtomicFact, FactType
        fact = AtomicFact(
            content=content,
            fact_type=FactType.EPISODIC,
            memory_id=mem_id,
            profile_id=mock_engine._profile_id,
            scope=scope,
            shared_with=shared_with,
        )
        mock_engine.store_fact_direct(fact)


def test_materializer_extracts_scope_from_metadata(mock_engine):
    """pending 记录带 scope=global metadata → 材质化后 AtomicFact.scope=global."""
    pending = [{
        "id": 1,
        "content": "global knowledge",
        "metadata": _json.dumps({"scope": "global"}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    # Verify store_fact_direct called with correct scope
    assert mock_engine.store_fact_direct.called
    fact = mock_engine.store_fact_direct.call_args[0][0]
    assert fact.scope == "global"


def test_materializer_extracts_shared_with(mock_engine):
    """pending 记录带 shared_with metadata → 材质化后 fact.shared_with 正确."""
    pending = [{
        "id": 2,
        "content": "shared knowledge",
        "metadata": _json.dumps({"scope": "shared", "shared_with": ["agent_a", "agent_b"]}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    assert mock_engine.store_fact_direct.called
    fact = mock_engine.store_fact_direct.call_args[0][0]
    assert fact.scope == "shared"
    assert fact.shared_with == ["agent_a", "agent_b"]

    # Verify memories INSERT included shared_with
    # call_args_list: [0]=dedup SELECT, [1]=memories INSERT, [2]=store_fact_direct (not db call)
    insert_call = mock_engine._db.execute.call_args_list[1]
    sql, params = insert_call[0]
    assert "shared_with" in sql
    # params: mem_id, profile_id, content, session_id, speaker, role, created_at, scope, shared_with, metadata_json
    assert params[7] == "shared"
    assert _json.loads(params[8]) == ["agent_a", "agent_b"]


def test_materializer_default_scope_personal(mock_engine):
    """pending 记录无 scope → 默认 personal."""
    pending = [{
        "id": 3,
        "content": "plain knowledge",
        "metadata": _json.dumps({"tags": ["test"]}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    assert mock_engine.store_fact_direct.called
    fact = mock_engine.store_fact_direct.call_args[0][0]
    assert fact.scope == "personal"
    assert fact.shared_with is None


def test_materializer_unknown_scope_preserved(mock_engine):
    """上游未校验的未知 scope → 保留原值（材质化线程不校验）."""
    pending = [{
        "id": 4,
        "content": "weird scope",
        "metadata": _json.dumps({"scope": "super_secret"}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    assert mock_engine.store_fact_direct.called
    fact = mock_engine.store_fact_direct.call_args[0][0]
    assert fact.scope == "super_secret"


def test_materializer_shared_with_non_list_ignored(mock_engine):
    """shared_with 不是 list → 视为 None."""
    pending = [{
        "id": 5,
        "content": "bad shared_with",
        "metadata": _json.dumps({"scope": "shared", "shared_with": "not_a_list"}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    assert mock_engine.store_fact_direct.called
    fact = mock_engine.store_fact_direct.call_args[0][0]
    assert fact.shared_with is None
    # memories INSERT should have None for shared_with
    # call_args_list: [0]=dedup SELECT, [1]=memories INSERT
    insert_call = mock_engine._db.execute.call_args_list[1]
    _sql, params = insert_call[0]
    assert params[8] is None


def test_materializer_metadata_scope_popped(mock_engine):
    """scope 和 shared_with 应该从 metadata 中 pop 掉，不重复写入 metadata_json."""
    pending = [{
        "id": 6,
        "content": "metadata clean",
        "metadata": _json.dumps({"scope": "global", "shared_with": ["a"], "tags": ["x"]}),
        "tags": "",
    }]
    _run_loop_once(mock_engine, pending)

    # The last db.execute is store_fact_direct (not a db call), so the last db.execute
    # before that is the memories INSERT.
    # call_args_list: [0]=dedup SELECT, [1]=memories INSERT
    insert_call = mock_engine._db.execute.call_args_list[1]
    _sql, params = insert_call[0]
    metadata_json = _json.loads(params[9])
    assert "scope" not in metadata_json
    assert "shared_with" not in metadata_json
    assert metadata_json.get("tags") == ["x"]
