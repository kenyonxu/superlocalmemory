# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""There is one recall in this system, and every door reaches it.

Field parity and default parity are both necessary and neither is sufficient:
two surfaces can advertise identical shapes and identical limits and still name
different memories, if each has its own way of finding them.

They do not, and that is the property worth protecting. The MCP tool does not
retrieve — it resolves the daemon and asks the same handler the HTTP route runs,
so the two cannot disagree by construction. What would break that is somebody
adding a "helpful" local fallback to the tool so it answers when the daemon is
down. It would answer differently, quietly, only on the machines where the
daemon had stopped.

So this pins the delegation rather than comparing two paths that are one path:
with no daemon reachable, MCP recall says so instead of answering.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


class _ToolCollector:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator


_FACTS = [
    ("f-alpha", "the migration runs before the daemon accepts connections"),
    ("f-beta", "the daemon refuses a profile switch it cannot confirm locally"),
    ("f-gamma", "a withheld row is never shown as a memory"),
    ("f-delta", "recall and search must return the same number of results"),
    ("f-epsilon", "the graph counts come from one query, in one place"),
]


@pytest.fixture
def seeded(engine_with_mock_deps):
    engine = engine_with_mock_deps
    engine.profile_id = "default"
    engine._config.active_profile = "default"
    engine._db.execute(
        "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)",
        ("default", "default"),
    )
    for fid, content in _FACTS:
        engine._db.execute(
            "INSERT INTO memories "
            "(memory_id, profile_id, content, session_id, speaker, role, "
            " created_at, metadata_json, scope) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"mem-{fid}", "default", content, "s", "user", "user",
             "2026-01-01T00:00:00Z", "{}", "personal"),
        )
        engine._db.execute(
            "INSERT INTO atomic_facts "
            "(fact_id, memory_id, profile_id, content, lifecycle) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, f"mem-{fid}", "default", content, "active"),
        )
    return engine


def _client(engine) -> TestClient:
    from superlocalmemory.server.profile_runtime import bind_profile_runtime
    from superlocalmemory.server.unified_daemon import create_app

    app = create_app()
    app.state.engine = engine
    app.state.config = engine._config
    bind_profile_runtime(app.state, engine, engine._config)
    return TestClient(app)


def _ids(payload) -> list[str]:
    """Fact ids out of whatever shape a surface wraps its results in."""
    if isinstance(payload, dict):
        for key in ("results", "memories", "facts", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    assert isinstance(payload, list), f"no result list in {type(payload)}: {payload!r}"
    out = []
    for row in payload:
        assert isinstance(row, dict), f"unexpected result row: {row!r}"
        ident = row.get("fact_id") or row.get("id") or row.get("memory_id")
        assert ident, f"result row carries no identity: {sorted(row)}"
        out.append(str(ident))
    return out


QUERY = "the daemon refuses a profile switch"


def test_the_http_route_answers_from_the_store(seeded) -> None:
    """The one implementation, exercised directly."""
    response = _client(seeded).get("/recall", params={"q": QUERY})
    assert response.status_code == 200, response.text
    ids = _ids(response.json())
    assert ids, "recall returned nothing on a seeded store"
    assert all(any(fid == seeded_id for seeded_id, _ in _FACTS) for fid in ids), (
        f"recall named facts that were never seeded: {ids}"
    )


def test_the_http_route_honours_an_explicit_limit(seeded) -> None:
    client = _client(seeded)
    for limit in (1, 3):
        response = client.get("/recall", params={"q": QUERY, "limit": limit})
        assert response.status_code == 200, response.text
        assert len(_ids(response.json())) <= limit


def test_mcp_recall_has_no_retrieval_of_its_own(seeded) -> None:
    """With the daemon unreachable, the tool reports that — it does not improvise.

    A local fallback here would be a second implementation of recall, reachable
    only on machines where the daemon had stopped, and it would answer
    differently from every other surface without anyone noticing. If this test
    ever fails because a fallback was added deliberately, the fallback needs a
    parity test of its own before it ships.
    """
    from superlocalmemory.mcp import tools_core

    collector = _ToolCollector()
    tools_core.register_core_tools(collector, lambda: seeded)
    result = asyncio.run(collector.tools["recall"](QUERY))

    assert result.get("success") is False, (
        f"MCP recall answered without a daemon: {str(result)[:300]}"
    )
    assert "daemon" in str(result.get("error", "")).lower(), (
        f"the refusal does not name the daemon: {result.get('error')!r}"
    )
