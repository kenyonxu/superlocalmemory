# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""One downloadable archive of the whole recovery unit, not one database of it.

The dashboard's "Export" gzipped ``memory.db`` and nothing else, under a page that
lists learning data, audit and code data, and pending operations as included. The
archive built here is the coordinated backup set (every managed store, copied
under one fence and checksummed, with its manifest) plus the picture and document
originals, and an ``EXPORT-MANIFEST.json`` that says what is in it and what was
left out on purpose, with the reason. Every stored checksum is read back from the
finished archive before it is offered.

Left out on purpose: ``config.json`` (it can hold provider API keys) and the
identity and access secrets. They are never bundled silently.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARCHIVE_FORMAT = "slm-backup-export"
ARCHIVE_VERSION = 1
ROOT = "slm-backup"

#: What an export never contains, and why. Shown in the manifest and on the page.
OMITTED: tuple[dict[str, str], ...] = (
    {"what": "config.json", "reason": "settings can hold provider API keys; copy it yourself, "
                                      "knowing what is in it"},
    {"what": "identity and access secrets",
     "reason": "install token, signing key, remote keys and certificates are never bundled"},
    {"what": "backup_obligations.db",
     "reason": "the record of erasures that no restore may undo stays with this installation"},
    {"what": "caches and derived indexes", "reason": "rebuilt from the stores above"},
)


class ArchiveVerificationError(RuntimeError):
    """The finished archive does not match the checksums it was built from."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(payload), int(datetime.now().timestamp()), 0o600
    tar.addfile(info, io.BytesIO(payload))


def _originals(data_root: Path) -> list[Path]:
    from superlocalmemory.media import files

    base = files.media_root(data_root)
    if not base.is_dir() or base.is_symlink():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink()
                  and files.is_original(p.relative_to(base).as_posix()))


def build_export(data_root: Path, destination: Path) -> dict[str, Any]:
    """Write the archive to ``destination`` (created owner-only) and return its manifest."""
    from superlocalmemory.infra.backup import MANAGED_DATABASES, BackupCoordinator

    data_root = Path(data_root)
    backup_set = BackupCoordinator(
        MANAGED_DATABASES, data_root, data_root / "backups").create_backup_set()
    set_dir = Path(backup_set.stores[0].file_path).parent if backup_set.stores else None
    with tempfile.TemporaryDirectory(prefix=".export-", dir=data_root / "backups") as scratch:
        if set_dir is not None:
            set_dir = _without_credentials(set_dir, Path(scratch) / "set")
        return _write_archive(data_root, destination, backup_set.set_id, set_dir)


def _without_credentials(set_dir: Path, copy: Path) -> Path:
    """A copy of the set with accounts, sessions and sign-in links removed.

    The local set keeps its bytes. Its ``manifest.json`` is rewritten for the
    copy, with the new checksums and manifest hash, so the exported set still
    verifies on its own.
    """
    from superlocalmemory.infra.backup_credentials import scrub_store

    shutil.copytree(set_dir, copy, symlinks=True)
    manifest_path = copy / "manifest.json"
    native = json.loads(manifest_path.read_text(encoding="utf-8"))
    for store in native.get("stores", []):
        path = copy / store["store_name"]
        if scrub_store(path, store["store_name"]):
            store["sha256"], store["size_bytes"] = _sha256(path), path.stat().st_size
    sums = "|".join(sorted(s["sha256"] for s in native.get("stores", [])))
    native["manifest_hash"] = hashlib.sha256(sums.encode()).hexdigest()
    manifest_path.write_text(json.dumps(native, indent=2), encoding="utf-8")
    return copy


def _write_archive(data_root: Path, destination: Path, set_id: str,
                   set_dir: Path | None) -> dict[str, Any]:
    from superlocalmemory.infra.backup_credentials import OMISSION
    from superlocalmemory.infra.private_files import open_private_new

    native = json.loads((set_dir / "manifest.json").read_text(encoding="utf-8")) if set_dir else {}
    stores = [{"name": s["store_name"], "sha256": s["sha256"], "bytes": s["size_bytes"]}
              for s in native.get("stores", [])]
    originals = _originals(data_root)
    manifest: dict[str, Any] = {
        "format": ARCHIVE_FORMAT, "version": ARCHIVE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "set_id": set_id, "stores": stores,
        "media_originals": len(originals), "omitted": [*OMITTED, OMISSION],
    }
    fd = open_private_new(destination)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        if set_dir is not None:
            tar.add(str(set_dir), arcname=f"{ROOT}/set", recursive=True)
        from superlocalmemory.media import files

        base = files.media_root(data_root)
        for path in originals:
            tar.add(str(path), arcname=f"{ROOT}/media/{path.relative_to(base).as_posix()}")
        _add_bytes(tar, f"{ROOT}/EXPORT-MANIFEST.json",
                   json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    verify_export(destination, manifest)
    return manifest


def verify_export(archive: Path, manifest: dict[str, Any]) -> None:
    """Re-read every store from the finished archive and compare its checksum."""
    expected = {f"{ROOT}/set/{s['name']}": s["sha256"] for s in manifest["stores"]}
    seen: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name in expected:
                digest = hashlib.sha256()
                handle = tar.extractfile(member)
                if handle is None:
                    raise ArchiveVerificationError(f"{member.name} cannot be read back")
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
                if digest.hexdigest() != expected[member.name]:
                    raise ArchiveVerificationError(f"{member.name} does not match its checksum")
                seen.add(member.name)
    missing = sorted(set(expected) - seen)
    if missing:
        raise ArchiveVerificationError(f"missing from the archive: {', '.join(missing)}")
