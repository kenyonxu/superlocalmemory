# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Where the mail, calendar and transcript importers may read from.

An importer turns file contents into memories that every reader of the
workspace can see, so the path it reads is a local-file read on behalf of
whoever set it. The dashboard may only point an importer at:

* ``<data root>/imports`` (created on demand), or the older drop folder
  ``<data root>/import`` the mail and calendar importers scan, or
* a folder the computer's owner approved on this machine with
  ``slm adapters allow-folder PATH``. That list lives in
  ``import-roots.json`` beside the stores and has no HTTP route.

A path is admitted only when its real location (symlinks resolved) is inside
one of those folders, no part of it below the folder is hidden (starts with
``.``), and it is not elsewhere inside the data root.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_FOLDER = "imports"
DROP_FOLDER = "import"
#: Adapter -> the setting that names the file or folder it reads.
PATH_FIELDS = {"transcript": "watch_dir", "gmail": "mbox_path", "calendar": "ics_path"}
_LABELS = {"transcript": "transcript folder", "gmail": "MBOX file", "calendar": "ICS file"}
APPROVED_FILE = "import-roots.json"
REFUSAL = ("Choose a file or folder inside an import folder: {imports}, or a folder "
           "approved on this computer with `slm adapters allow-folder PATH`.")


def default_root(data_root: Path) -> Path:
    return Path(data_root) / DEFAULT_FOLDER


def approved(data_root: Path) -> list[Path]:
    """The owner-approved folders (as stored), unreadable file = none."""
    try:
        roots = json.loads((Path(data_root) / APPROVED_FILE).read_text(encoding="utf-8"))
        return [Path(p) for p in roots.get("roots", []) if isinstance(p, str)]
    except (OSError, ValueError, AttributeError):
        return []


def roots(data_root: Path) -> list[Path]:
    """Every folder an importer may read from, resolved; missing ones skipped."""
    found = []
    for root in (default_root(data_root), Path(data_root) / DROP_FOLDER, *approved(data_root)):
        try:
            found.append(root.resolve(strict=True))
        except OSError:
            continue
    return found


def _under(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def admit(path: str | Path, data_root: Path) -> Path:
    """The real path to store, or ``ValueError`` saying where files may come from."""
    message = REFUSAL.format(imports=default_root(data_root))
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(message)
    try:
        real = candidate.resolve(strict=True)
        data = Path(data_root).resolve(strict=True)
    except OSError:
        raise ValueError(message) from None
    drops = (default_root(data_root).resolve(), (Path(data_root) / DROP_FOLDER).resolve())
    for root in roots(data_root):
        if not _under(real, root):
            continue
        if any(part.startswith(".") for part in real.relative_to(root).parts):
            continue
        if _under(real, data) and not any(_under(real, drop) for drop in drops):
            continue
        return real
    raise ValueError(message)


def approve(path: str | Path, data_root: Path) -> Path:
    """Add a folder the owner chose. Refuses the filesystem root and the data root."""
    real = Path(path).expanduser().resolve(strict=True)
    data = Path(data_root).resolve()
    if not real.is_dir():
        raise ValueError("Choose an existing folder.")
    if real == Path(real.anchor) or real == Path.home().resolve():
        raise ValueError("Choose a specific folder, not the whole disk or home folder.")
    if _under(real, data) or _under(data, real):
        raise ValueError("The SuperLocalMemory data folder cannot be an import folder.")
    stored = [str(p) for p in approved(data_root)]
    if str(real) not in stored:
        stored.append(str(real))
    target = Path(data_root) / APPROVED_FILE
    from superlocalmemory.storage._durable_json import write_json_atomic

    write_json_atomic(target, {"roots": stored})
    return real


def saved_path_error(name: str, config: dict, data_root: Path) -> str:
    """Why a path saved in adapters.json must not be read, or "" when it may.

    Paths saved before the import-folder rule (or edited by hand) get the same
    check as a new one. The message names no local path: readers see it too.
    """
    value = config.get(PATH_FIELDS.get(name, ""), "")
    if not value:
        return ""
    try:
        admit(value, data_root)
    except ValueError:
        label = _LABELS.get(name, "file")
        return (f"The saved {label} is outside the import folders, so it will not be read. "
                "Move it into the imports folder in SuperLocalMemory's data folder, or "
                "approve its folder with `slm adapters allow-folder <folder>`, then choose "
                "it again in Configure.")
    return ""


def saved_path(name: str, config: dict, data_root: Path) -> str:
    """The admitted path a worker may read, or "" (logged) when it may not."""
    import logging

    error = saved_path_error(name, config, data_root)
    if error:
        logging.getLogger(__name__).error("%s adapter: %s", name, error)
        return ""
    value = config.get(PATH_FIELDS.get(name, ""), "")
    return str(admit(value, data_root)) if value else ""
