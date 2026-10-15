# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A workspace-scoped approval installs into that workspace, never the shared source."""

import hashlib

from superlocalmemory.evolution.skill_activator import SkillActivator


def test_workspace_activation_ignores_the_installed_source_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    installed = tmp_path / ".claude" / "plugins" / "pack" / "demo" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("original", encoding="utf-8")
    root = tmp_path / "ws"
    quarantine = root / "quarantine"
    (quarantine / "demo-v1").mkdir(parents=True)
    body = "evolved"
    (quarantine / "demo-v1" / "SKILL.md").write_text(body, encoding="utf-8")
    activator = SkillActivator(
        live_root=root / "skills", backup_root=root / "backups",
        quarantine_root=quarantine, installation_wide=False,
    )
    result = activator.activate(
        "demo", "demo-v1", actor_id="t",
        expected_hash=hashlib.sha256(body.encode()).hexdigest(),
        source_path=str(installed),
    )
    assert installed.read_text(encoding="utf-8") == "original"
    assert str(root / "skills") in result["live_path"]
