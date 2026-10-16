"""PR-247/4: hashed backup paths and files cannot escape through symlinks."""

import hashlib
import json

import pytest

from superlocalmemory.evolution.skill_activator import SkillActivator


@pytest.mark.parametrize("operation", ["activate", "rollback"])
@pytest.mark.parametrize("link", ["directory", "backup", "manifest"])
def test_symlinked_backup_destination_is_refused(tmp_path, operation, link):
    live, quarantine, backups, outside = [
        tmp_path / name for name in ("live", "quarantine", "backups", "outside")
    ]
    for root in (live, quarantine, backups, outside):
        root.mkdir()
    (live / "review").mkdir()
    live_file = live / "review" / "SKILL.md"
    live_file.write_text("original instructions")
    (quarantine / "review-v1").mkdir()
    (quarantine / "review-v1" / "SKILL.md").write_text("new instructions")
    directory = backups / ("review-" + hashlib.sha256(b"review").hexdigest()[:16])
    if link == "directory":
        directory.symlink_to(outside, target_is_directory=True)
    else:
        directory.mkdir()
        filename = "SKILL.md.bak" if link == "backup" else "SKILL.md.target.json"
        target = outside / filename
        target.write_text(
            json.dumps({"live_path": str(live_file)}) if link == "manifest" else "outside sentinel"
        )
        (directory / filename).symlink_to(target)
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    activator = SkillActivator(live_root=live, backup_root=backups, quarantine_root=quarantine)
    with pytest.raises(ValueError):
        if operation == "activate":
            activator.activate("review", "review-v1")
        else:
            activator.rollback("review")
    assert live_file.read_text() == "original instructions"
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_legacy_rollback_backup_symlink_is_refused(tmp_path):
    live, backups, outside = [tmp_path / name for name in ("live", "backups", "outside")]
    for root in (live, backups, outside):
        (root / "review").mkdir(parents=True)
    target = outside / "review" / "secret"
    target.write_text("outside sentinel")
    (backups / "review" / "SKILL.md.bak").symlink_to(target)
    with pytest.raises(ValueError):
        SkillActivator(live_root=live, backup_root=backups).rollback("review")
