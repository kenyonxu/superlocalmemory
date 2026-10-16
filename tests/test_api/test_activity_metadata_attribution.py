"""Recent activity keeps connection attribution while supporting legacy tables."""

import json
import sqlite3

from superlocalmemory.server.routes import agents, helpers
from tests.test_api.test_agent_memory_activity import _seed


def test_recent_activity_preserves_connection_and_claimed_attribution(tmp_path, monkeypatch):
    db = tmp_path / "memory.db"
    _seed(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE ingestion_operations ADD COLUMN raw_metadata_json TEXT")
        for operation, name, source in (
            ("op1", "connected-agent", "connection"),
            ("op2", "claimed-agent", "claimed"),
        ):
            conn.execute(
                "UPDATE ingestion_operations SET raw_metadata_json=?, trusted_actor_id=? "
                "WHERE operation_id=?",
                (json.dumps({"agent_id": name, "agent_id_source": source}),
                 "daemon-capability:" + "a" * 40, operation),
            )
    monkeypatch.setattr(agents, "DB_PATH", db)
    monkeypatch.setattr(helpers, "get_active_profile", lambda: "default")

    result = agents.get_agent_memory_activity(request=None, limit=20)

    assert result["total_memories"] == 4
    assert [row["agent_id"] for row in result["recent"]] == [
        "unknown", "gemini", "claimed-agent", "connected-agent",
    ]
    claimed, connected = result["recent"][-2:]
    assert claimed["verified"] is False
    assert connected["verified"] is True
    assert claimed["writer"] == connected["writer"] == "daemon-capability:aaaaaa"
    cards = {row["agent_id"]: row for row in result["agents"]}
    assert cards["connected-agent"]["verified"] == 1
    assert cards["claimed-agent"]["verified"] == 0
    assert cards["claimed-agent"]["claimed"] == 1
