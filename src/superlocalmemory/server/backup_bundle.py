"""Portable verified recovery units built by the managed multi-store coordinator."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from superlocalmemory.infra.backup import (
    MANAGED_DATABASES,
    BackupCoordinator,
    BackupSetManifest,
    StoreEntry,
)
from superlocalmemory.infra.backup_credentials import (
    CREDENTIAL_TABLES,
    OMISSION,
    carry_forward,
    scrub_store,
)
from superlocalmemory.infra.backup_credentials import capture as capture_credentials
from superlocalmemory.infra.private_files import create_private_file
from superlocalmemory.server.config_file import read_config, update_config
from superlocalmemory.storage._durable_json import write_json_atomic

SETTINGS = (
    "config.json",
    "mode_a.json",
    "mode_b.json",
    "mode_c.json",
    "backup_config.json",
    "adapters.json",
)


#: A setting name that holds a secret: the whole name, or its last ``_`` part(s).
#: Matching substrings dropped ``max_tokens``, ``token_budget`` and ``cache_key``
#: from every recovery point (PR 251 review, finding 9).
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|passphrase|credentials?|token|"
    r"recovery_key|backup_key|private_key|access_key|signing_key|embedding_key)$"
    r"|^key$",
    re.IGNORECASE,
)


def _is_secret(name: str, value) -> bool:
    """A value under a secret name, unless it is a flag or a number (never a secret)."""
    if value is None or isinstance(value, (bool, int, float)):
        return False
    return bool(_SECRET_NAME.search(name))


def _safe_settings(value):
    if isinstance(value, dict):
        return {k: _safe_settings(v) for k, v in value.items() if not _is_secret(k, v)}
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    if isinstance(value, list):
        return [_safe_settings(v) for v in value]
    return value


def _point_dir(root: Path, point_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{16}", point_id):
        raise ValueError("Invalid recovery backup")
    parent = root / "backups"
    point = parent / f"backup_{point_id}"
    if point.is_symlink() or point.resolve().parent != parent.resolve():
        raise ValueError("Invalid recovery backup")
    return point


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(point):
    files = []
    for path in sorted(point.rglob("*")):
        rel = path.relative_to(point).as_posix()
        if (
            path.is_file()
            and not path.is_symlink()
            and rel not in ("manifest.json", "recovery.json")
        ):
            files.append({"name": rel, "bytes": path.stat().st_size, "sha256": _hash(path)})
    return files


def create_point(root: Path, keep: frozenset[str] = frozenset()) -> dict:
    """Take a verified recovery point; retention never removes the ids in ``keep``."""
    from superlocalmemory.server.config_file import _file_lock
    with _file_lock(Path(root) / "recovery-sets"):
        return _create_point(Path(root), keep)


def _create_point(root: Path, keep: frozenset[str] = frozenset()) -> dict:
    root = Path(root)
    coordinator = BackupCoordinator(MANAGED_DATABASES, root, root / "backups")
    native = coordinator.create_backup_set()
    point = _point_dir(root, native.set_id)
    for name in SETTINGS:
        src = root / name
        if src.is_file():
            write_json_atomic(point / "settings" / name, _safe_settings(read_config(src)))
    mirror = root / "backups" / "media-originals"
    if mirror.is_dir():
        from superlocalmemory.infra.backup_media import _originals

        for rel, src in _originals(mirror).items():
            dst = point / "media-originals" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            create_private_file(dst)
            shutil.copyfile(src, dst)
    manifest = {
        "format": 1,
        "id": native.set_id,
        "created": native.created_at,
        "stores": [entry.store_name for entry in native.stores],
        "files": _files(point),
        "omissions": ["credentials", "downloaded models and runtime caches"],
        "absent_stores": [
            name for name in MANAGED_DATABASES if name not in {e.store_name for e in native.stores}
        ],
    }
    write_json_atomic(point / "recovery.json", manifest)
    verify_point(root, native.set_id)
    from superlocalmemory.server.recovery_inventory import prune
    prune(root, keep)
    return manifest


def verify_point(root: Path, point_id: str) -> dict:
    point = _point_dir(Path(root), point_id)
    try:
        manifest = json.loads((point / "recovery.json").read_text(encoding="utf-8"))
        if manifest["format"] != 1 or manifest["id"] != point_id or not manifest["files"]:
            raise ValueError("invalid manifest")
        for entry in manifest["files"]:
            path = point / entry["name"]
            if path.is_symlink() or not path.resolve().is_relative_to(point.resolve()):
                raise ValueError("invalid member")
            if (
                not path.is_file()
                or path.stat().st_size != entry["bytes"]
                or _hash(path) != entry["sha256"]
            ):
                raise ValueError("checksum mismatch")
        expected = {entry["name"] for entry in manifest["files"]}
        if len(expected) != len(manifest["files"]):
            raise ValueError("duplicate member")
        if expected != {entry["name"] for entry in _files(point)}:
            raise ValueError("unlisted member")
        if any(p.is_symlink() for p in point.rglob("*")):
            raise ValueError("linked member")
        if (
            not set(manifest["stores"]) <= set(MANAGED_DATABASES)
            or not set(manifest["stores"]) <= expected
        ):
            raise ValueError("missing store")
        return {
            **manifest,
            "verified": True,
            "size_mb": sum(e["bytes"] for e in manifest["files"]) / 1024**2,
        }
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Backup verification failed. Nothing was restored; create a new recovery backup."
        ) from exc


def export_point(root: Path, point_id: str) -> Path:
    """Zip a verified point, with accounts, sessions and the sign-in setting removed.

    The point on disk keeps its bytes; each store that holds credential tables
    is copied, scrubbed (``infra.backup_credentials``) and listed in the
    exported manifest with its new checksum, so the archive still verifies.
    """
    manifest = verify_point(root, point_id)
    point = _point_dir(Path(root), point_id)
    backups = Path(root) / "backups"
    fd, name = tempfile.mkstemp(prefix="slm-recovery-", suffix=".zip", dir=backups)
    import os

    os.close(fd)
    export = Path(name)
    try:
        with tempfile.TemporaryDirectory(prefix=".export-", dir=backups) as scratch:
            files = [_export_member(point, entry, Path(scratch)) for entry in manifest["files"]]
            exported = {**manifest, "files": [entry for entry, _ in files],
                        "excluded": [OMISSION]}
            exported.pop("verified", None)
            exported.pop("size_mb", None)
            with zipfile.ZipFile(export, "w", zipfile.ZIP_DEFLATED) as archive:
                for entry, source in files:
                    archive.write(source, entry["name"])
                archive.writestr("recovery.json", json.dumps(exported))
        return export
    except Exception:
        export.unlink(missing_ok=True)
        raise


def _export_member(point: Path, entry: dict, scratch: Path) -> tuple[dict, Path]:
    source = point / entry["name"]
    if entry["name"] not in CREDENTIAL_TABLES:
        return entry, source
    copy = scratch / entry["name"]
    shutil.copyfile(source, copy)
    if not scrub_store(copy, entry["name"]):
        return entry, source
    return {**entry, "bytes": copy.stat().st_size, "sha256": _hash(copy)}, copy


def _merge_settings(current, restored):
    fields = sorted(k for k in set(current) | set(restored)
                    if k.endswith(("endpoint", "provider", "backend", "_url", "_host"))
                    or k in ("api_base", "base_url"))
    old_destination = tuple(current.get(k) for k in fields)
    new_destination = tuple(restored.get(k, current.get(k)) for k in fields)
    if old_destination != new_destination:
        for key in list(current):
            if _is_secret(key, current[key]):
                current[key] = ""
    for key, value in restored.items():
        if key in (
            "base_dir",
            "port",
            "daemon_port",
            "daemon_legacy_port",
            "daemon_enable_legacy_port",
        ):
            continue
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            _merge_settings(current[key], value)
        else:
            current[key] = value


def restore_point(root: Path, point_id: str, *, settings: bool = True) -> None:
    """Restore the stores (and, unless ``settings`` is False, the settings) of a point.

    ``settings=False`` is the rollback path: the caller puts the settings
    files back byte for byte instead of merging them.
    """
    root = Path(root)
    manifest = verify_point(root, point_id)
    point = _point_dir(root, point_id)
    entries = tuple(
        StoreEntry(name, str(point / name), (point / name).stat().st_size, _hash(point / name))
        for name in manifest["stores"]
    )
    coordinator = BackupCoordinator(MANAGED_DATABASES, root, root / "backups")
    native = BackupSetManifest(
        point_id, 0, entries, coordinator._compute_manifest_hash(entries), True
    )
    # Existing coordinator owns safety copies, rollback and erasure-obligation replay.
    # Keep the coordinator's shared-media mirror out of this restore. Original
    # bytes are authorized against the post-erasure catalog below.
    # Accounts, sessions and the sign-in policy are never rolled back by a
    # restore: the ones this installation has now carry forward.
    accounts = capture_credentials(root)
    with tempfile.TemporaryDirectory(prefix=".offline-restore-", dir=root / "backups") as isolated:
        offline = BackupCoordinator(MANAGED_DATABASES, root, Path(isolated))
        offline.restore_from_manifest(native)
    carry_forward(root, accounts)
    if settings:
        _restore_settings(root, point)
    _restore_surviving_originals(root, point, manifest)


def _restore_settings(root: Path, point: Path) -> None:
    for name in SETTINGS:
        src = point / "settings" / name
        if src.is_file():
            restored = json.loads(src.read_text(encoding="utf-8"))
            update_config(root / name, lambda current: _merge_settings(current, restored))
    if (root / "config.json").exists():
        mode = read_config(root / "config.json").get("mode", "a")
        from superlocalmemory.core.config import SLMConfig

        SLMConfig.write_current_mode(mode, root)


def _restore_surviving_originals(root, point, manifest):
    from superlocalmemory.infra.backup_media import _copy_private, _originals
    from superlocalmemory.media import open_media_store

    originals = _originals(point / "media-originals")
    if not originals:
        return
    # Erasure obligations have already been replayed. Only the surviving
    # catalog can authorize original bytes to come back; unreadable fails shut.
    store = open_media_store(data_root=root)
    if store is None:
        raise ValueError("Media recovery refused: its catalog could not be read")
    try:
        surviving = store.known_relpaths()
    finally:
        store.close()
    verified_names = {entry["name"] for entry in manifest["files"]}
    for rel, src in originals.items():
        if rel in surviving and "media-originals/" + rel in verified_names:
            _copy_private(src, root / "media" / rel)
