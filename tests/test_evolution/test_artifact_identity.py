"""Candidate artifacts retain verified bytes and the discovered host path."""

import hashlib
from pathlib import Path

import pytest

from superlocalmemory.evolution import skill_evolver as se
from superlocalmemory.evolution.skill_activator import SkillActivator
from superlocalmemory.evolution.types import EvolutionCandidate, EvolutionType, TriggerType


@pytest.mark.parametrize("kind", [EvolutionType.DERIVED, EvolutionType.CAPTURED])
def test_each_candidate_has_a_distinct_artifact(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(se, "EVOLVED_SKILLS_DIR", tmp_path / "quarantine")
    evolver = se.SkillEvolver(tmp_path / "memory.db")
    candidate = EvolutionCandidate("review", kind, TriggerType.HEALTH_CHECK)
    first, _ = evolver._write_evolved_skill(candidate, "---\nname: review\n---\nfirst", "first")
    second, _ = evolver._write_evolved_skill(candidate, "---\nname: review\n---\nsecond", "second")
    assert first != second
    assert first.read_text().endswith("first")


def test_changed_artifact_is_refused_before_backup(tmp_path):
    quarantine = tmp_path / "quarantine"
    artifact = quarantine / "review-v1" / "SKILL.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("changed bytes")
    live = tmp_path / "live" / "review" / "SKILL.md"
    live.parent.mkdir(parents=True)
    live.write_text("original")
    activator = SkillActivator(
        live_root=tmp_path / "live", backup_root=tmp_path / "backups", quarantine_root=quarantine
    )
    with pytest.raises(ValueError, match="digest"):
        activator.activate(
            "review", "review-v1", expected_hash=hashlib.sha256(b"verified bytes").hexdigest()
        )
    assert live.read_text() == "original"
    assert not (tmp_path / "backups" / "review" / "SKILL.md.bak").exists()


def test_recursive_codex_skill_activates_and_rolls_back_at_its_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    original = (
        tmp_path
        / ".codex"
        / "plugins"
        / "cache"
        / "plugin"
        / "1.0"
        / "skills"
        / "review"
        / "SKILL.md"
    )
    original.parent.mkdir(parents=True)
    original.write_text("original instructions")
    evolver = se.SkillEvolver(tmp_path / "memory.db")
    assert evolver._read_skill_content("plugin:review") == "original instructions"
    source = evolver._skill_sources["plugin:review"]
    quarantine = tmp_path / "quarantine"
    artifact = quarantine / "review-v1" / "SKILL.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("improved instructions")
    activator = SkillActivator(backup_root=tmp_path / "backups", quarantine_root=quarantine)
    activator.activate("plugin:review", "review-v1", source_path=source)
    assert original.read_text() == "improved instructions"
    activator.rollback("plugin:review")
    assert original.read_text() == "original instructions"


def test_namespace_never_selects_another_plugins_same_leaf(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = tmp_path / ".codex" / "plugins" / "cache"
    wrong = cache / "aaa-vendor" / "plugin-b" / "1.0" / "skills" / "review" / "SKILL.md"
    correct = cache / "zzz-vendor" / "plugin-a" / "1.0" / "skills" / "review" / "SKILL.md"
    for path, content in ((wrong, "wrong plugin"), (correct, "right plugin")):
        path.parent.mkdir(parents=True)
        path.write_text(content)
    glob = Path.glob
    monkeypatch.setattr(
        Path,
        "glob",
        lambda root, pattern: iter(sorted(glob(root, pattern), key=lambda path: path != wrong)),
    )
    evolver = se.SkillEvolver(tmp_path / "memory.db")
    assert evolver._read_skill_content("plugin-a:review") == "right plugin"
    assert evolver._skill_sources["plugin-a:review"] == str(correct)


def test_rollback_identity_does_not_collide_after_sanitization(tmp_path):
    live = tmp_path / "live"
    first = live / "pkg" / "leaf" / "SKILL.md"
    second = live / "pkg-leaf" / "SKILL.md"
    quarantine = tmp_path / "quarantine"
    for target, original in ((first, "first original"), (second, "second original")):
        target.parent.mkdir(parents=True)
        target.write_text(original)
    for name, body in (("one", "first mutation"), ("two", "second mutation")):
        artifact = quarantine / name / "SKILL.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(body)
    activator = SkillActivator(
        live_root=live, backup_root=tmp_path / "backups", quarantine_root=quarantine
    )
    activator.activate("pkg:leaf", "one", source_path=str(first))
    activator.activate("pkg-leaf", "two", source_path=str(second))
    activator.rollback("pkg:leaf")
    assert first.read_text() == "first original"
    assert second.read_text() == "second mutation"


def test_legacy_backup_uses_its_original_configured_root(tmp_path):
    live = tmp_path / "live"
    target = live / "review" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("legacy active")
    backup = tmp_path / "backups" / "review" / "SKILL.md.bak"
    backup.parent.mkdir(parents=True)
    backup.write_text("legacy original")
    activator = SkillActivator(live_root=live, backup_root=tmp_path / "backups")
    activator.rollback("review")
    assert target.read_text() == "legacy original"
