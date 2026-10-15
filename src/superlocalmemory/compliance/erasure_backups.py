# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Backups still hold an erased profile: record the obligation, never drop it.

An erasure cannot rewrite a backup (its checksums would stop matching and the
copy would no longer restore), so each snapshot that holds the profile is
recorded as an obligation: it is replayed on any restore, reported as an
outstanding item of the erasure, and ends only when the snapshot is gone from
disk or has been read again and found clean. A snapshot that cannot be read is
recorded too, as ``unreadable``: not being able to look is not the same as
there being nothing to find.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Folders under the data root that hold full copies of the stores.
SNAPSHOT_DIRS = ("backups", "pre-migration-snapshots")

#: Folders of copies that are not SQLite files (the graph and vector stores a
#: scale-engine promotion keeps for rollback). They hold entity names and
#: vectors and cannot be read here, so each copy is an obligation of kind
#: ``unreadable`` until it is deleted.
OPAQUE_DIRS = ("scale-backups",)


def _opaque_copies(root: Path) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for name in OPAQUE_DIRS:
        folder = Path(root) / name
        if not folder.is_dir() or folder.is_symlink():
            continue
        for child in sorted(folder.iterdir()):
            if not child.is_symlink():
                found.append((str(child), int(child.stat().st_mtime)))
    return found


def retention_days_from_config() -> int:
    """The configured obligation retention window, 90 days by default."""
    try:
        from superlocalmemory.core.config import SLMConfig

        cfg = SLMConfig()
        for attr in ("backup_retention_days", "obligation_retention_days",
                     "retention_window_days", "backup_obligation_retention_days"):
            val = getattr(cfg, attr, None)
            if isinstance(val, int) and val > 0:
                return val
        for attr in dir(cfg):
            if attr.startswith("_"):
                continue
            sub = getattr(cfg, attr, None)
            if not hasattr(sub, "__dict__") and not hasattr(sub, "__dataclass_fields__"):
                continue
            for sub_attr in dir(sub):
                if any(h in sub_attr.lower() for h in ("retention", "retain")):
                    v = getattr(sub, sub_attr, None)
                    if isinstance(v, int) and v > 0:
                        return v
    except Exception:  # noqa: BLE001
        pass
    return 90


def record_backup_obligations(
    data_root: Path, profile_id: str, erasure_id: str, counts: dict,
) -> int:
    """Record every snapshot that holds, or may hold, ``profile_id``; return pending count.

    Raises when the scan itself fails (the caller marks ``backup_scan_failed``).
    """
    from superlocalmemory.infra import backup_obligations as bom

    retention_days = retention_days_from_config()
    store = bom.BackupObligationStore(data_root)
    hits: list[tuple[str, int]] = []
    unreadable: list[tuple[str, int]] = []
    for name in SNAPSHOT_DIRS:
        report = bom.scan_backup_snapshots(Path(data_root) / name, profile_id)
        hits.extend(report.hits)
        unreadable.extend(report.unreadable)
    unreadable.extend(_opaque_copies(Path(data_root)))
    counts["backup_snapshots_scanned"] = len(hits)
    recorded = 0
    for kind, found in (("contains", hits), ("unreadable", unreadable)):
        for snap_path, snap_epoch in found:
            try:
                store.record(profile_id=profile_id, erasure_id=erasure_id,
                             snapshot_path=snap_path, snapshot_epoch=snap_epoch,
                             retention_days=retention_days, kind=kind)
            except Exception as exc:  # noqa: BLE001
                logger.error("GDPR erase: failed to record obligation for a snapshot: %s", exc)
                counts["backup_record_errors"] = counts.get("backup_record_errors", 0) + 1
            recorded += 1
    if unreadable:
        counts["backup_snapshots_unreadable"] = len(unreadable)

    # A restore's leftovers hold the same words (deltas lose this profile's rows
    # now; every pre-restore copy that still holds it is registered). Raises on
    # failure, which blocks the completeness claim.
    from superlocalmemory.storage._restore_retention import cover_profile_erasure

    covered = cover_profile_erasure(data_root, profile_id, erasure_id=erasure_id,
                                    store=store, retention_days=retention_days)
    counts.update(covered)
    recorded += covered["pre_restore_copies_registered"]
    counts["backup_obligations_recorded"] = recorded

    # Re-read what the ledger holds, so a snapshot removed or already clean
    # since an earlier erasure ends its obligation on evidence, not on age.
    store.reconcile(profile_id)
    overdue = store.list_overdue(profile_id)
    if overdue:
        counts["backup_obligations_overdue"] = len(overdue)
    return store.count_pending(profile_id)
