"""Both evolution producers and automatic activation preserve workspace scope."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from superlocalmemory.evolution.blind_verifier import VerificationResult
from tests.test_evolution.test_phase2_governance import _make_candidate, _make_evolver


def test_automatic_workspace_activation_receipts_scope(tmp_path):
    evolver = _make_evolver(tmp_path)
    evolver._config = SimpleNamespace(evolution=SimpleNamespace(auto_approve=True))
    activator = MagicMock()
    activator._installation_wide = False
    activator.activate.return_value = {
        "live_path": str(tmp_path / "live" / "SKILL.md"),
        "content_hash": "test-hash",
        "backup_path": None,
    }
    evolver._activator = activator
    with (
        patch.object(evolver, "_llm_confirm", return_value=True),
        patch.object(evolver, "_generate_mutation", return_value="# evolved"),
        patch.object(
            evolver,
            "_blind_verify",
            return_value=VerificationResult(passed=True, confidence=0.9, reasoning="test"),
        ),
        patch.object(evolver, "_read_skill_content", return_value="original"),
        patch.object(
            evolver,
            "_write_evolved_skill",
            return_value=(tmp_path / "q" / "SKILL.md", "candidate-dir"),
        ),
    ):
        assert evolver._process_candidate(_make_candidate(), "default") == "evolved"
    record = evolver._store.get_recent("default")[0]
    receipt = evolver._store.get_transitions(record.id, "default")[-1]
    assert json.loads(receipt["metadata"])["installation_wide"] is False


def test_mcp_evolution_constructs_a_scoped_producer(tmp_path, monkeypatch):
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.mcp.tools_evolution import register_evolution_tools

    state_path("config.json").write_text(json.dumps({"evolution": {"enabled": True}}))
    registered = {}

    class Server:
        def tool(self, **kwargs):
            def add(fn):
                registered[fn.__name__] = fn
                return fn

            return add

    register_evolution_tools(Server(), lambda: SimpleNamespace(profile_id="sales"))
    captured = {}

    class Evolver:
        def __init__(self, db, config, **kwargs):
            captured.update(kwargs)
            self._store = MagicMock()
            self._store.get_skill_history.return_value = []

        def evolve_candidate(self, candidate, profile):
            assert profile == "sales"
            return "quarantined"

    monkeypatch.setattr("superlocalmemory.evolution.skill_evolver.SkillEvolver", Evolver)
    asyncio.run(registered["evolve_skill"](skill_name="same-skill"))
    assert captured["profile_id"] == "sales"
    assert captured["activator"]._installation_wide is False
    assert captured["activator"]._quarantine_root != state_path("quarantine") / "skills"
