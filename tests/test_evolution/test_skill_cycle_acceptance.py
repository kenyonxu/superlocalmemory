"""WP11 acceptance: mocked local mutation, separate execution, approval and undo."""

import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from superlocalmemory.evolution import skill_evolver as se
from superlocalmemory.evolution.budget import EvolutionBudget
from superlocalmemory.evolution.evolution_store import EvolutionStore
from superlocalmemory.evolution.skill_activator import SkillActivator
from superlocalmemory.evolution.types import EvolutionCandidate, EvolutionType, TriggerType
from superlocalmemory.server.routes import evolution as route
from tests.test_evolution.test_skill_evolver_firing import _provision_cost_log

MUTATION = """---
name: normalize
description: Sort and deduplicate integer values.
---
For a list of integers, return its sorted unique values.
Reference implementation:
```python
def normalize(values):
    return sorted(set(values))
```
"""


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    live = tmp_path / "live"
    skill = live / "normalize" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    original = "---\nname: normalize\n---\nSort the values.\n"
    skill.write_text(original)
    quarantine = tmp_path / "quarantine"
    activator = SkillActivator(
        live_root=live, backup_root=tmp_path / "backups", quarantine_root=quarantine
    )
    monkeypatch.setattr(se, "EVOLVED_SKILLS_DIR", quarantine)
    monkeypatch.setattr(se, "_ollama_running", lambda: True)
    monkeypatch.setattr(se, "_ollama_models", lambda: ("llama3.2:latest", "qwen3:latest"))
    dispatched = []

    def model(prompt, *, model, max_tokens, **kwargs):
        dispatched.append(model)
        if max_tokens == 100:
            return json.dumps({"decision": "approve", "reason": "reproduce missing deduplication"})
        if max_tokens == 4000:
            return "<EVOLUTION_COMPLETE>\n" + MUTATION
        return json.dumps({"passed": True, "confidence": 0.9})

    monkeypatch.setattr(se, "_dispatch_llm", model)
    cfg = SimpleNamespace(evolution=SimpleNamespace(backend="ollama", auto_approve=False))
    _provision_cost_log(tmp_path / "learning.db")
    budget = EvolutionBudget(
        profile_id="default", learning_db=tmp_path / "learning.db", lock_dir=tmp_path / "locks"
    )
    evolver = se.SkillEvolver(
        tmp_path / "memory.db", cfg, audit_chain=MagicMock(), activator=activator, budget=budget
    )
    monkeypatch.setattr(evolver, "_read_skill_content", lambda name: original)
    return evolver, skill, original, activator, dispatched


def test_full_local_skill_cycle(cycle, tmp_path, monkeypatch):
    evolver, skill, original, activator, dispatched = cycle
    quarantine = tmp_path / "quarantine"
    outcome = evolver.evolve_candidate(
        EvolutionCandidate("normalize", EvolutionType.FIX, TriggerType.HEALTH_CHECK)
    )
    assert outcome == "quarantined"
    assert len(set(dispatched)) == 2
    record = EvolutionStore(tmp_path / "memory.db").get_recent("default")[0]
    artifact = quarantine / record.quarantine_dir_name / "SKILL.md"
    # Independent execution in a separate interpreter, before human approval.
    code = re.search(r"```python\n(.*?)```", artifact.read_text(), re.S)[1]
    check = subprocess.run(
        [sys.executable, "-I", "-c", code + "\nassert normalize([3,1,3,2]) == [1,2,3]"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert check.returncode == 0, check.stderr
    assert skill.read_text() == original
    monkeypatch.setattr(route, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(route, "get_active_profile", lambda: "default")
    monkeypatch.setattr(route, "_require_manage", lambda request, **_: None)
    monkeypatch.setattr(
        "superlocalmemory.evolution.skill_activator.SkillActivator", lambda: activator
    )
    # Writing back to the installed skill is an installation-wide approval;
    # a workspace approval installs into that workspace's own folder.
    monkeypatch.setattr(route, "_activator", lambda installation_wide, *_: activator)
    approved = route.evolution_approve(
        None, route.ApproveSkillRequest(record_id=record.id, installation_wide=True)
    )
    assert approved["success"], approved
    assert skill.read_text() == MUTATION.strip()
    rolled = route.evolution_rollback(
        None, route.RollbackSkillRequest(skill_name="normalize", record_id=record.id)
    )
    assert rolled["success"]
    assert skill.read_text() == original


def test_auto_approval_digest_refusal_keeps_live_original(cycle, monkeypatch):
    evolver, skill, original, activator, dispatched = cycle
    evolver._config.evolution.auto_approve = True
    write = evolver._write_evolved_skill

    def tamper(*args):
        path, name = write(*args)
        path.write_text("changed after verification")
        return path, name

    monkeypatch.setattr(evolver, "_write_evolved_skill", tamper)
    outcome = evolver.evolve_candidate(
        EvolutionCandidate("normalize", EvolutionType.FIX, TriggerType.HEALTH_CHECK)
    )
    assert outcome == "rejected"
    assert skill.read_text() == original
