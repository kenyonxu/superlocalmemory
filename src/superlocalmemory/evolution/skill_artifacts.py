# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Quarantined artifacts and skill discovery, shared across local hosts."""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from superlocalmemory.evolution.types import EvolutionCandidate, EvolutionType


class SkillArtifacts:
    def _artifact_root(self) -> Path:
        # The activator owns the quarantine root for this workspace; without
        # one, the installation-wide evolved-skills directory is used.
        activator = getattr(self, "_activator", None)
        if activator is not None:
            return activator._quarantine_root
        from superlocalmemory.evolution.skill_evolver import EVOLVED_SKILLS_DIR

        return EVOLVED_SKILLS_DIR

    def _read_skill_content(self, skill_name: str) -> str:
        """Resolve a source within its installed host and package namespace."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*", skill_name):
            return ""
        activator = getattr(self, "_activator", None)
        if activator is not None:
            live = activator._live_path(skill_name)
            if live.is_file():
                return live.read_text(encoding="utf-8")
        parts = skill_name.split(":")
        namespace = parts[:-1]
        roots = (
            Path.home() / ".codex" / "skills",
            Path.home() / ".codex" / "plugins" / "cache",
            Path.home() / ".claude" / "skills",
            Path.home() / ".claude" / "plugins",
        )
        for root in roots:
            if not root.exists():
                continue
            for match in root.glob(f"**/{parts[-1]}/SKILL.md"):
                if not all(
                    part.casefold() in {p.casefold() for p in match.parts} for part in namespace
                ):
                    continue
                if not match.resolve().is_relative_to(root.resolve()):
                    continue
                try:
                    content = match.read_text(encoding="utf-8")
                except OSError:
                    continue
                self._skill_sources = {
                    **getattr(self, "_skill_sources", {}),
                    skill_name: str(match.resolve()),
                }
                return content
        return ""

    def _write_evolved_skill(
        self,
        candidate: EvolutionCandidate,
        content: str,
        record_id: str,
    ) -> tuple[Path, str]:
        """Write a candidate-specific artifact and metadata into quarantine."""
        self._artifact_root().mkdir(parents=True, exist_ok=True)

        # Build directory name. skill_name derives from a behavioral-assertion
        # trigger (any non-whitespace), so sanitize for ALL evolution types —
        # "../../.claude/settings" must not escape self._artifact_root().
        base_name = re.sub(r"[^a-zA-Z0-9_-]", "-", candidate.skill_name).lower()[:50] or "skill"
        if candidate.evolution_type == EvolutionType.FIX:
            dir_name = f"{base_name}-v{record_id[:6]}"
        elif candidate.evolution_type == EvolutionType.DERIVED:
            # Extract name from evolved content frontmatter
            name_match = re.search(r"name:\s*(.+)", content)
            dir_name = name_match.group(1).strip() if name_match else f"{base_name}-derived"
            dir_name = re.sub(r"[^a-zA-Z0-9_-]", "-", dir_name).lower()[:50]
        else:
            dir_name = base_name
        dir_name = dir_name or "skill"

        dir_name = f"{dir_name}-v{record_id}"
        skill_dir = self._artifact_root() / dir_name
        # Defense in depth: never write outside the evolved-skills directory.
        if not skill_dir.resolve().is_relative_to(self._artifact_root().resolve()):
            raise ValueError(f"evolved skill path escapes sandbox: {dir_name!r}")
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8", newline="")

        # Write metadata sidecar (CRIT-2: includes quarantine_dir_name for recovery)
        self._write_metadata(skill_dir, dir_name, candidate, record_id)

        return skill_path, dir_name

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _extract_description(self, content: str) -> str:
        """Extract description from SKILL.md frontmatter."""
        match = re.search(r"description:\s*(.+?)(?:\n|---)", content)
        return match.group(1).strip() if match else "Skill for AI agent tasks"

    def _compute_diff(self, original: str, evolved: str) -> str:
        if not original:
            return "(new skill — no original to diff)"
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            evolved.splitlines(keepends=True),
            fromfile="original",
            tofile="evolved",
            n=3,
        )
        return "".join(diff)

    def _summarize_diff(self, diff: str) -> str:
        """Count additions and removals."""
        additions = sum(
            1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        removals = sum(
            1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
        )
        return f"+{additions}/-{removals} lines"

    def _write_metadata(self, skill_dir, dir_name, candidate, record_id):
        meta = {
            "skill_id": dir_name,
            "parent_skill_id": candidate.skill_name,
            "evolution_type": candidate.evolution_type.value,
            "trigger": candidate.trigger.value,
            "record_id": record_id,
            "quarantine_dir_name": dir_name,
            "evidence": list(candidate.evidence),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (skill_dir / ".skill_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
