"""Cross-agent scope integration tests — personal isolation, global visibility, shared_with."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Engine-level store/recall tests
# ---------------------------------------------------------------------------


def test_store_personal_default(engine_with_mock_deps):
    """Storing without scope creates personal-scope facts."""
    engine = engine_with_mock_deps
    fact_ids = engine.store("React 19 is our framework")
    assert len(fact_ids) > 0

    rows = engine._db.execute(
        "SELECT scope FROM atomic_facts WHERE fact_id = ?", (fact_ids[0],)
    )
    assert len(rows) > 0
    assert rows[0]["scope"] == "personal"


def test_store_global(engine_with_mock_deps):
    """Storing with scope='global' creates global-scope facts."""
    engine = engine_with_mock_deps
    fact_ids = engine.store("Project uses React 19", scope="global")
    assert len(fact_ids) > 0

    rows = engine._db.execute(
        "SELECT scope FROM atomic_facts WHERE fact_id = ?", (fact_ids[0],)
    )
    assert rows[0]["scope"] == "global"


def test_store_shared_with(engine_with_mock_deps):
    """Storing with shared_with creates personal-scope facts with JSON array."""
    engine = engine_with_mock_deps
    fact_ids = engine.store("API endpoint changed", shared_with=["agent_b"])
    assert len(fact_ids) > 0

    rows = engine._db.execute(
        "SELECT scope, shared_with FROM atomic_facts WHERE fact_id = ?", (fact_ids[0],)
    )
    assert rows[0]["scope"] == "personal"
    assert rows[0]["shared_with"] is not None
    assert "agent_b" in rows[0]["shared_with"]


def test_recall_includes_global(engine_with_mock_deps):
    """Recall with include_global=True can return global-scope facts."""
    engine = engine_with_mock_deps
    engine.store("Global knowledge: Python 3.12 released", scope="global")

    result = engine.recall("Python", include_global=True, include_shared=False)
    assert result is not None


def test_recall_excludes_global_when_disabled(engine_with_mock_deps):
    """Recall with include_global=False only returns personal-scope facts from DB."""
    engine = engine_with_mock_deps

    engine.store("My personal debugging technique")
    engine.store("Debugging best practices globally", scope="global")

    # Verify at DB level: personal-only query excludes global
    personal_facts = engine._db.get_all_facts(
        engine.profile_id, scope="personal", include_global=False, include_shared=False,
    )
    for f in personal_facts:
        assert "globally" not in f.content


# ---------------------------------------------------------------------------
# DB-level cross-agent isolation tests
# ---------------------------------------------------------------------------


def test_cross_agent_isolation_db(in_memory_db):
    """Verify personal facts are invisible to other agents at DB level."""
    from superlocalmemory.storage.database import DatabaseManager

    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('a', 'A')"
    )
    in_memory_db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('b', 'B')"
    )
    # Insert memories first (FK parent for atomic_facts.memory_id)
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m1', 'a', 'source', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope) "
        "VALUES ('m2', 'a', 'source', 'global')"
    )
    in_memory_db.execute(
        "INSERT INTO memories (memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('m3', 'a', 'source', 'personal', '[\"b\"]')"
    )
    # Now insert facts referencing those memories
    in_memory_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f1', 'm1', 'a', 'secret', 'personal')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope) "
        "VALUES ('f2', 'm2', 'a', 'public info', 'global')"
    )
    in_memory_db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, scope, shared_with) "
        "VALUES ('f3', 'm3', 'a', 'shared to b', 'personal', '[\"b\"]')"
    )
    in_memory_db.commit()

    # Agent B: personal only — no visibility into A's data
    where_b, params_b = DatabaseManager._scope_where("b", "personal", False, False)
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_b}", params_b
    ).fetchall()
    assert all(r["content"] != "secret" for r in rows)

    # Agent B: include global — sees public info but not A's personal
    where_bg, params_bg = DatabaseManager._scope_where("b", "personal", True, False)
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_bg}", params_bg
    ).fetchall()
    contents = [r["content"] for r in rows]
    assert "public info" in contents
    assert "secret" not in contents

    # Agent B: include shared — sees facts shared with B
    where_bs, params_bs = DatabaseManager._scope_where("b", "personal", False, True)
    rows = in_memory_db.execute(
        f"SELECT content FROM atomic_facts WHERE {where_bs}", params_bs
    ).fetchall()
    contents = [r["content"] for r in rows]
    assert "shared to b" in contents


def test_scope_where_helper():
    """Verify _scope_where produces correct SQL conditions."""
    from superlocalmemory.storage.database import DatabaseManager

    # Personal only (no global, no shared)
    clause, params = DatabaseManager._scope_where("alice", "personal", False, False)
    assert "scope = 'personal'" in clause
    assert params[0] == "alice"
    assert "global" not in clause
    assert "json_each" not in clause

    # Personal + global
    clause, params = DatabaseManager._scope_where("alice", "personal", True, False)
    assert "scope = 'global'" in clause

    # Personal + shared
    clause, params = DatabaseManager._scope_where("alice", "personal", False, True)
    assert "json_each" in clause


# ---------------------------------------------------------------------------
# MCP pending-store scope propagation
# ---------------------------------------------------------------------------


def test_mcp_remember_scope_in_pending(tmp_path, monkeypatch):
    """Verify MCP remember tool stores scope in pending metadata."""
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))

    from unittest.mock import MagicMock

    from superlocalmemory.cli.pending_store import get_pending
    from superlocalmemory.mcp.tools_core import register_core_tools

    server = MagicMock()
    tools = {}

    def capture_tool(annotations=None):
        def decorator(fn):
            tools[fn.__name__] = fn
            return fn
        return decorator

    server.tool = capture_tool

    mock_engine = MagicMock()
    mock_engine.profile_id = "test"
    register_core_tools(server, lambda: mock_engine)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        tools["remember"]("test content", scope="global", shared_with="agent_b,agent_c")
    )

    assert result["success"] is True
    assert result["pending"] is True

    pending = get_pending(limit=1)
    assert len(pending) > 0

    md = json.loads(pending[0].get("metadata") or "{}")
    assert md.get("scope") == "global"
    assert md.get("shared_with") == ["agent_b", "agent_c"]


# ---------------------------------------------------------------------------
# WorkerPool IPC propagation
# ---------------------------------------------------------------------------


def test_worker_pool_recall_passes_scope_flags():
    """Verify WorkerPool.recall() includes scope flags in the command dict."""
    from unittest.mock import MagicMock, patch

    from superlocalmemory.core.worker_pool import WorkerPool

    pool = WorkerPool()
    sent_payload = {}

    def mock_send(req):
        sent_payload.update(req)
        return {"ok": True, "results": [], "result_count": 0}

    pool._send = mock_send

    pool.recall("test query", include_global=False, include_shared=True)

    assert sent_payload.get("include_global") is False
    assert sent_payload.get("include_shared") is True
