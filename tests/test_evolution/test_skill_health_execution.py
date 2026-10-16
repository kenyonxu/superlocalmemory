"""BRN-07: downstream failures and invocation-only traces are not healthy."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from superlocalmemory.mcp.tools_evolution import register_evolution_tools
from tests.mcp.test_loop_tools import _Capture


def events(path, outcome):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE tool_events (id INTEGER PRIMARY KEY,profile_id TEXT,session_id TEXT, "
            "tool_name TEXT,event_type TEXT,input_summary TEXT,output_summary TEXT, "
            "project_path TEXT,created_at TEXT,duration_ms INTEGER);"
            "CREATE TABLE behavioral_assertions (profile_id TEXT,category TEXT, "
            "trigger_condition TEXT,action TEXT,confidence REAL);"
        )
        for i in range(6):
            conn.execute(
                "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    i * 2 + 1,
                    "default",
                    f"s{i}",
                    "Skill",
                    "call",
                    json.dumps({"skill": "review"}),
                    "",
                    "/p",
                    f"2026-10-01T12:0{i}:00",
                    1,
                ),
            )
            if outcome:
                conn.execute(
                    "INSERT INTO tool_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        i * 2 + 2,
                        "default",
                        f"s{i}",
                        "Bash",
                        outcome,
                        "",
                        "tests failed" if outcome == "error" else "tests passed",
                        "/p",
                        f"2026-10-01T12:0{i}:01",
                        1,
                    ),
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,status", [("error", "critical"), ("success", "healthy"), ("", "insufficient_evidence")]
)
async def test_health_reflects_execution(tmp_path, monkeypatch, outcome, status):
    path = tmp_path / "memory.db"
    events(path, outcome)
    monkeypatch.setattr("superlocalmemory.mcp.tools_evolution.state_path", lambda name: path)
    server = _Capture()
    register_evolution_tools(server, lambda: SimpleNamespace(profile_id="default"))
    result = await server.fns["skill_health"]("review")
    skill = result["skills"][0]
    assert skill["status"] == status
    assert skill["sample_count"] == (6 if outcome else 0)
    assert skill["effective_score"] == (-1 if outcome == "error" else 1 if outcome else 0)
