# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Skill artifact routing shared by HTTP and MCP evolution producers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from superlocalmemory.evolution.skill_activator import SkillActivator


def workspace_activator(
    base_dir: Path,
    profile_id: str,
    *,
    installation_wide: bool = False,
    quarantine_dir_name: str = "",
) -> SkillActivator:
    """Keep workspace files separate; legacy verified sources remain readable."""
    key = hashlib.sha256(profile_id.encode("utf-8")).hexdigest()
    root = base_dir / "workspace_skills" / key
    quarantine = root / "quarantine"
    scoped = SkillActivator(quarantine_root=quarantine)
    if quarantine_dir_name and not scoped._quarantine_path(quarantine_dir_name).is_file():
        quarantine = base_dir / "quarantine" / "skills"
    if installation_wide:
        return SkillActivator(quarantine_root=quarantine)
    return SkillActivator(
        live_root=root / "skills",
        backup_root=root / "backups",
        quarantine_root=quarantine,
        installation_wide=False,
    )
