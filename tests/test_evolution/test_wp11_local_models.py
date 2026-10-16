"""Configured local model names and Codex directories reach evolution."""

from types import SimpleNamespace

from superlocalmemory.evolution.llm_dispatch import _validate_model
from superlocalmemory.evolution.model_selection import resolve_evolution_models
from superlocalmemory.evolution.skill_evolver import SkillEvolver


def test_installed_local_model_can_be_selected_and_dispatched():
    cfg = SimpleNamespace(
        mutation_model="ollama:llama3.2:latest",
        verify_model="ollama:qwen3:latest",
        confirm_model="ollama:llama3.2:latest",
    )
    selected = resolve_evolution_models(cfg, "ollama", ollama_available=True)
    assert selected.mutation == cfg.mutation_model
    assert selected.verify == cfg.verify_model
    _validate_model(selected.mutation)
    _validate_model(selected.verify)


def test_codex_skill_is_discovered(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    skill = tmp_path / ".codex" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Review tests before approval.")
    evolver = SkillEvolver(tmp_path / "memory.db")
    assert evolver._read_skill_content("review") == skill.read_text()


def test_default_ollama_uses_installed_inventory():
    cfg = SimpleNamespace(mutation_model="", verify_model="", confirm_model="")
    selected = resolve_evolution_models(
        cfg, "ollama", ollama_available=True, installed_models=("llama3.2:latest",)
    )
    assert selected.mutation == "ollama:llama3.2:latest"
    assert selected.verify == selected.mutation
    assert selected.independent is False
