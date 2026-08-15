"""MCP compatibility contract for the shared Living Brain truth model."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from superlocalmemory.mcp.tools_brain import register_brain_tools


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


class _Engine:
    profile_id = "alpha"


def _create_memory_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE atomic_facts (
                fact_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE correction_cases (
                case_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )


def _create_learning_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE learning_signals (profile_id TEXT NOT NULL, signal_type TEXT NOT NULL);
            CREATE TABLE agent_experiences (
                profile_id TEXT NOT NULL,
                experience_id TEXT NOT NULL,
                verification_authority TEXT NOT NULL
            );
            CREATE TABLE cognitive_turn_receipts (
                profile_id TEXT NOT NULL,
                receipt_id TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE external_evidence_receipts (
                profile_id TEXT NOT NULL,
                run_ref TEXT NOT NULL,
                run_state TEXT NOT NULL,
                demonstration INTEGER NOT NULL,
                eligible_for_learning INTEGER NOT NULL
            );
            """
        )


def test_mcp_brain_status_uses_brain_truth_and_keeps_v404_aliases(
    tmp_path: Path, monkeypatch
) -> None:
    """The new read model is canonical while v4.0.4 keys remain usable."""
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    _create_memory_db(tmp_path / "memory.db")
    _create_learning_db(tmp_path / "learning.db")
    with sqlite3.connect(tmp_path / "learning.db") as conn:
        conn.execute("INSERT INTO agent_experiences VALUES ('alpha', 'e1', 'human_approval')")
        conn.execute("INSERT INTO cognitive_turn_receipts VALUES ('alpha', 't1', 'finalized')")
        conn.execute(
            "INSERT INTO external_evidence_receipts VALUES ('alpha', 'run-1', 'SUCCEEDED', 1, 0)"
        )

    server = _Server()
    register_brain_tools(server, lambda: _Engine())
    status = asyncio.run(server.tools["get_brain_evidence_status"]())

    truth = status["brain_truth"]
    assert truth["contract"] == "superlocalmemory.brain-truth/v1"
    assert truth["profile_id"] == "alpha"
    assert truth["control_plane"] == "observation_only"
    assert truth["agent_experience"]["claimed_experiences_total"] == 1
    assert truth["external_evidence"]["receipts_total"] == 1
    assert status["external_evidence"] == truth["external_evidence"]

    # v4.0.4 aliases remain for one release; observation is never a trainer.
    assert status["agent_experience"] == {
        "is_real": True,
        "availability": "available",
        "experiences_total": 1,
        "turns_total": 1,
        "turns_by_state": {"finalized": 1},
        "claimed_evidence_experiences": 1,
        "source": "learning.db:M040_agent_experience_receipts",
    }
    assert status["external_graph_evidence"] == {
        "is_real": True,
        "availability": "available",
        "total": 1,
        "by_run_state": {"SUCCEEDED": 1},
        "demonstrations": 1,
        "control_plane": "observation_only",
    }
    assert status["control_plane"] == "observation_only"
