"""Publish the selected template before its authoritative mode marker."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from superlocalmemory.server.config_file import read_config, update_config


def publish_selection(config) -> None:
    root = Path(config.base_dir)
    mode = config.mode.value
    saved = read_config(root / "config.json")
    if saved.get("mode") != mode:
        raise ValueError("Runtime mode was not persisted")
    # The marker is the commit point: an interruption before replacement keeps
    # readers on the previous template; afterward they see this complete copy.
    update_config(root / f"mode_{mode}.json", lambda data: (data.clear(), data.update(saved)))
    fd, name = tempfile.mkstemp(prefix=".current-mode-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / "current_mode")
    finally:
        Path(name).unlink(missing_ok=True)
