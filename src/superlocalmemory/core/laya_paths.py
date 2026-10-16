# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Offline guards for managed paths and non-relocatable Python environments."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

REPAIR = "The Python environment moved. Choose repair to rebuild it in this data root."


def _command_destination_matches(value: str, venv: Path) -> bool:
    # stdlib venv joins diagnostic command arguments without shell quoting.
    # Its final destination may contain spaces; do not split that path apart.
    if value.rstrip().endswith(' ' + str(venv)):
        return True
    args = shlex.split(value.strip())
    return not args or Path(args[-1]).resolve() == venv.resolve()


def _contained(path: Path, root: Path) -> bool:
    return path.is_relative_to(root) and path.parent.resolve().is_relative_to(root.resolve())


def managed_record(record: dict[str, Any], root: Path) -> dict[str, Any]:
    """Resolve root-relative marker paths; never inspect a foreign installation."""
    result = dict(record)
    for key in ('python', 'model_path', 'hf_home'):
        value = str(record.get(key, ''))
        if not value:
            continue
        path = Path(value)
        path = path if path.is_absolute() else root / path
        local = _contained(path, root) and '..' not in path.parts
        if key != 'python':
            local = local and path.resolve().is_relative_to(root.resolve())
        if not local:
            result['_path_error'] = REPAIR
            result[key] = str(root / 'venv' / 'bin' / 'python') if key == 'python' else ''
        else:
            result[key] = str(path)
    return result


def venv_refusal(venv: Path) -> str:
    """Reject copied command destinations, launchers, and foreign venv symlinks.

    A base-system interpreter and its ``home`` are legitimate external paths.
    The environment destination and Python entry points must stay local.
    """
    if venv.resolve().parent != venv.parent.resolve():
        return REPAIR
    cfg = venv / 'pyvenv.cfg'
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('home', 'executable'):
                target = Path(value.strip())
                if ('venv' in target.parts or 'runtimes' in target.parts):
                    if not target.is_relative_to(venv):
                        return REPAIR
            if sep and key.strip() == 'command':
                if not _command_destination_matches(value, venv):
                    return REPAIR
        for folder in (venv / 'bin', venv / 'Scripts'):
            if not folder.resolve().is_relative_to(venv.resolve()):
                return REPAIR
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.is_symlink():
                    target = path.resolve()
                    if 'venv' in target.parts and not target.is_relative_to(venv.resolve()):
                        return REPAIR
                    continue
                if not path.is_file():
                    continue
                with path.open('rb') as stream:
                    header = stream.read(512).split(b'\n', 1)[0]
                if header.startswith(b'#!') and b'python' in header.lower():
                    target = Path(header[2:].decode(errors='replace').strip())
                    if target.is_absolute() and target.parent.resolve() != folder.resolve():
                        return REPAIR
    except (OSError, ValueError):
        return REPAIR if cfg.exists() else ''
    return ''
