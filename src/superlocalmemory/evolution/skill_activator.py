# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""SkillActivator — atomic activation of quarantined evolved skills.

Copies a VERIFIED_QUARANTINED skill artifact into the live skill directory
(~/.claude/skills/{skill_name}/SKILL.md), retains the prior artifact as a
.bak file, and provides a tested rollback path.

Invariants:
  1. The destination directory is a child of live_root — path traversal raises ValueError.
  2. The backup is written BEFORE the live copy is overwritten.
  3. Activation is atomic via a .tmp file → os.replace (POSIX atomic on same fs).
  4. Rollback restores from the .bak file and leaves the live copy unchanged.
  5. Only the quarantined artifact identified by quarantine_dir_name is copied;
     no arbitrary file can be activated.

CRIT-2 note: skill_name and quarantine_dir_name are ALWAYS different strings:
  - skill_name: the original skill identifier (e.g. "brainstorming")
  - quarantine_dir_name: the sanitized quarantine subdir (e.g. "brainstorming-vabc12")
Both are required; neither defaults from the other.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from superlocalmemory.evolution import backup_files
from superlocalmemory.infra.data_root import canonical_data_root

logger = logging.getLogger(__name__)

LIVE_SKILLS_ROOT: Path = Path.home() / ".claude" / "skills"
BACKUP_ROOT: Path = canonical_data_root() / "skill_backups"
QUARANTINE_ROOT: Path = canonical_data_root() / "quarantine" / "skills"

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


class SkillActivationError(Exception):
    """Raised when activation fails after writing the backup.

    The caller must invoke rollback() after receiving this error to restore
    the previous live artifact.
    """


class SkillActivator:
    """Activates a quarantined evolved skill into the live skill directory."""

    def __init__(
        self,
        *,
        live_root: Path | None = None,
        backup_root: Path | None = None,
        quarantine_root: Path | None = None,
        installation_wide: bool = True,
    ) -> None:
        self._installation_wide = installation_wide
        self._live_root = live_root or LIVE_SKILLS_ROOT
        self._backup_root = backup_root or BACKUP_ROOT
        self._quarantine_root = quarantine_root or QUARANTINE_ROOT

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _safe_skill_dir(self, skill_name: str, base: Path) -> Path:
        """Return base / sanitized_name, raising ValueError on traversal.

        Two-layer check (fail-fast + belt-and-suspenders):
          1. Reject the raw skill_name if it contains '..' or is absolute.
          2. After sanitization, resolve the target path and verify it remains
             inside base.  This catches any edge case the regex misses.
        """
        # Fail-fast: detect traversal in raw name before sanitization.
        raw = Path(skill_name)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError(f"skill_name {skill_name!r} contains path traversal sequences")
        safe = _SAFE_NAME_RE.sub("-", skill_name).lower()[:50] or "skill"
        target = (base / safe).resolve()
        base_resolved = base.resolve()
        # Audit P2-5: is_relative_to is not prefix-fragile (a sibling like
        # ".../skills-evil" cannot masquerade as being under ".../skills").
        if not target.is_relative_to(base_resolved):
            raise ValueError(f"skill_name {skill_name!r} escapes sandbox: {target}")
        return target

    def _quarantine_path(self, quarantine_dir_name: str) -> Path:
        """Return the SKILL.md path inside quarantine for a given dir name.

        quarantine_dir_name is the sanitized directory name (CRIT-2), e.g.
        'brainstorming-vabc12'.  It is NOT the skill_name.

        Hardened (audit P0-1 / P1-4): reject absolute paths and '..' traversal,
        then resolve() and require containment inside the quarantine root. The
        resolve() step also defeats a symlinked quarantine dir that would
        otherwise redirect the read outside the sandbox and inject arbitrary
        content into a live skill.
        """
        raw = Path(quarantine_dir_name)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError(f"quarantine_dir_name {quarantine_dir_name!r} contains path traversal")
        root = self._quarantine_root.resolve()
        q_dir = (self._quarantine_root / quarantine_dir_name).resolve()
        if not q_dir.is_relative_to(root):
            raise ValueError(
                f"quarantine_dir_name {quarantine_dir_name!r} escapes sandbox: {q_dir}"
            )
        q_path = (q_dir / "SKILL.md").resolve()
        if not q_path.is_relative_to(root):
            raise ValueError(f"quarantine artifact escapes sandbox: {q_path}")
        return q_path

    def _live_path(self, skill_name: str) -> Path:
        root = self._live_root
        if root == LIVE_SKILLS_ROOT:
            codex_root = Path.home() / ".codex" / "skills"
            codex_path = self._safe_skill_dir(skill_name, codex_root) / "SKILL.md"
            if codex_path.is_file():
                root = codex_root
        return self._safe_skill_dir(skill_name, root) / "SKILL.md"

    def _backup_path(self, skill_name: str) -> Path:
        safe = self._safe_skill_dir(skill_name, self._backup_root)
        digest = hashlib.sha256(skill_name.encode()).hexdigest()[:16]
        backup = safe.with_name(f"{safe.name}-{digest}") / "SKILL.md.bak"
        backup_files.checked(backup, self._backup_root)
        backup_files.checked(backup.with_suffix(".target.json"), self._backup_root)
        return backup

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def activate(
        self,
        skill_name: str,
        quarantine_dir_name: str,
        *,
        actor_id: str = "",
        expected_hash: str = "",
        source_path: str = "",
    ) -> dict:
        """Verify bytes, preserve the original, then atomically install them."""
        q_path = self._quarantine_path(quarantine_dir_name)
        # A workspace-scoped activation never writes back to the installed
        # source: that would change the skill for every workspace.
        live_path = (
            self._source_target(source_path)
            if source_path and self._installation_wide
            else self._live_path(skill_name)
        )
        backup_path = self._backup_path(skill_name)
        if not q_path.exists():
            raise FileNotFoundError(f"Quarantine artifact not found: {q_path}")
        content = q_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if expected_hash and digest != expected_hash:
            raise ValueError("verified artifact digest mismatch")
        backup_written = self._backup_live(live_path, backup_path)
        self._remember_target(backup_path, live_path)
        self._atomic_write(live_path, content)
        logger.info("Activated skill %s (actor=%s)", skill_name, actor_id)
        return {
            "skill_name": skill_name,
            "live_path": str(live_path),
            "backup_path": str(backup_path) if backup_written else None,
            "content_hash": digest,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "actor_id": actor_id,
        }

    def _backup_live(self, live_path, backup_path):
        if live_path.exists():
            backup_files.write(backup_path, live_path.read_bytes(), self._backup_root)
            return True
        return False

    def _atomic_write(self, live_path, content):
        live_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = live_path.with_suffix(".tmp")
        try:
            tmp_path.write_bytes(content)
            os.replace(tmp_path, live_path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise SkillActivationError(f"atomic write failed for {live_path}: {exc}") from exc

    def rollback(self, skill_name: str) -> dict:
        """Restore the complete identity's recorded host target."""
        backup_path, live_path = self._rollback_paths(skill_name)
        if backup_path.exists():
            self._atomic_write(live_path, backup_files.read(backup_path, self._backup_root))
            restored = str(backup_path)
        elif live_path.exists():
            live_path.unlink()
            restored = None
        else:
            return {"skill_name": skill_name, "rolled_back": False, "note": "nothing to rollback"}
        return {
            "skill_name": skill_name,
            "rolled_back": True,
            "restored_from": restored,
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        }

    def _rollback_paths(self, skill_name):
        backup = self._backup_path(skill_name)
        manifest = backup.with_suffix(".target.json")
        if not backup.exists() and not manifest.exists():
            backup = self._safe_skill_dir(skill_name, self._backup_root) / "SKILL.md.bak"
            manifest = backup.with_suffix(".target.json")
        backup_files.checked(backup, self._backup_root)
        backup_files.checked(manifest, self._backup_root)
        if manifest.is_file():
            live = self._source_target(
                json.loads(backup_files.read(manifest, self._backup_root))["live_path"]
            )
        else:
            live = self._safe_skill_dir(skill_name, self._live_root) / "SKILL.md"
        return backup, live

    def _source_target(self, source_path: str) -> Path:
        target = Path(source_path).resolve()
        roots = (
            self._live_root,
            Path.home() / ".codex" / "skills",
            Path.home() / ".codex" / "plugins" / "cache",
            Path.home() / ".claude" / "plugins",
        )
        if target.name != "SKILL.md" or not any(
            target.is_relative_to(root.resolve()) for root in roots
        ):
            raise ValueError("skill source escapes the installed host directories")
        return target

    def _remember_target(self, backup_path, live_path):
        manifest = backup_path.with_suffix(".target.json")
        backup_files.write(
            manifest, json.dumps({"live_path": str(live_path)}).encode(), self._backup_root
        )
