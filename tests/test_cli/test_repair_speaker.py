"""#265 repair: find agent memories whose facts say "the user", re-save as the agent."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from superlocalmemory.cli import repair_speaker_cmd as rs


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE memories (memory_id TEXT, profile_id TEXT, content TEXT, session_id TEXT,"
        " role TEXT, session_date TEXT, metadata_json TEXT, scope TEXT);"
        "CREATE TABLE atomic_facts (memory_id TEXT, profile_id TEXT, content TEXT);")
    rows = [
        ("agent-bad", "I prefer short answers.", "user", {"agent_id": "ava"}, "the user prefers short answers"),
        ("agent-ok", "I prefer short answers.", "assistant", {"agent_id": "ava"}, "ava prefers short answers"),
        ("human", "I prefer dark mode.", "user", {}, "the user prefers dark mode"),
        ("quoted", 'Sam said "I hate tabs".', "user", {"agent_id": "ava"}, "the user hates tabs"),
        ("third", "The user prefers tea.", "user", {"agent_id": "ava"}, "the user prefers tea"),
    ]
    for mid, content, role, meta, fact in rows:
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?)",
                     (mid, "default", content, "s1", role, "2026-10-01", json.dumps(meta), "personal"))
        conn.execute("INSERT INTO atomic_facts VALUES (?,?,?)", (mid, "default", fact))
    conn.commit()
    conn.close()
    return path


def test_only_agent_first_person_is_a_candidate(tmp_path: Path) -> None:
    found = rs.find_candidates(_db(tmp_path), "default")
    assert [c.memory_id for c in found] == ["agent-bad"]
    assert found[0].agent == "ava"


def test_resave_speaks_as_the_agent_and_replaces(tmp_path: Path, monkeypatch) -> None:
    sent = {}

    def fake(method, path, body, **kw):
        sent.update(body)
        return {"status": "stored", "fact_ids": ["f1"]}

    import superlocalmemory.cli.daemon as d
    monkeypatch.setattr(d, "daemon_request", fake)
    rs._resave("agent-bad", "ava", _db(tmp_path), "default")
    assert sent["speaker"] == "ava" and sent["replaces"] == "agent-bad"
    assert sent["content"] == "I prefer short answers."
    assert sent["idempotency_key"] == "repair-speaker:agent-bad"
