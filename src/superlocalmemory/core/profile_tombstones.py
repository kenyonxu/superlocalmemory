# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""A workspace's name and description live in two places; an erasure must clear both.

``profiles.json`` (the profile manager's registry, also written by the dashboard
and the command line) holds a workspace's name and description, which can name a
person or a project. Erasing a profile deleted its row in the database and left
this entry, and the dashboard's profile listing then "repaired" the database
from it, so an erased workspace came back within one page load.

Removing the entry is not enough on its own: a process that loaded the registry
before the erasure writes it back whole. So an erased name is also recorded as a
tombstone, and every writer and every repair consults it. The tombstone holds a
salted digest of the name, not the name, and an explicit creation of a profile
with that name lifts it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_FILE = "profiles.json"
TOMBSTONE_FILE = "erased_profiles.json"
_SALT = "slm-erased-profile:v1:"


def digest(name: str) -> str:
    return hashlib.sha256((_SALT + name).encode("utf-8")).hexdigest()


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, payload: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def erased_digests(root: Path) -> set[str]:
    try:
        data = _read(Path(root) / TOMBSTONE_FILE)
    except (OSError, ValueError):
        return set()
    return set(data.get("digests", [])) if isinstance(data, dict) else set()


def is_erased(root: Path, name: str) -> bool:
    return digest(name) in erased_digests(root)


def record(root: Path, name: str) -> None:
    """Remember that ``name`` was erased. Raises on failure: an unrecorded erasure can be undone."""
    root = Path(root)
    digests = erased_digests(root) | {digest(name)}
    _atomic_write(root / TOMBSTONE_FILE, {"digests": sorted(digests)})


def clear(root: Path, name: str) -> None:
    """Lift the tombstone: someone is deliberately creating a profile of this name."""
    root = Path(root)
    digests = erased_digests(root)
    if digest(name) in digests:
        _atomic_write(root / TOMBSTONE_FILE, {"digests": sorted(digests - {digest(name)})})


def _entry_names(entry: Any) -> set[str]:
    if not isinstance(entry, dict):
        return set()
    return {str(entry[k]) for k in ("name", "profile_id") if entry.get(k)}


def remove_from_registry(root: Path, name: str) -> bool:
    """Delete ``name`` from ``profiles.json`` (both layouts) and the active pointer.

    Returns True when nothing of the profile is left in the file afterwards.
    """
    path = Path(root) / REGISTRY_FILE
    data = _read(path)
    if not isinstance(data, dict):
        return True
    changed = False
    profiles = data.get("profiles")
    if isinstance(profiles, list):
        kept = [p for p in profiles if name not in _entry_names(p)]
        changed = len(kept) != len(profiles)
        data["profiles"] = kept
    elif isinstance(profiles, dict):
        for key in [k for k, v in profiles.items() if k == name or name in _entry_names(v)]:
            del profiles[key]
            changed = True
    for pointer in ("active", "active_profile"):
        if data.get(pointer) == name:
            data[pointer] = "default"
            changed = True
    if changed:
        _atomic_write(path, data)
    return name not in json.dumps(_read(path), ensure_ascii=False)


def erase_profile_entry(root: Path, name: str) -> bool:
    """Tombstone first, then clean the registry; True when both are done."""
    try:
        record(root, name)
        return remove_from_registry(root, name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile registry could not be cleaned: %s", type(exc).__name__)
        return False
