# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Refuse a half-applied hand edit. API saves replace the entire live snapshot.

A settings file changed by hand is refused until restart or a dashboard Save.
No query is sent with the old settings; callers get a clear restart instruction.
"""

from __future__ import annotations

from pathlib import Path

from superlocalmemory.core import answer_check_state

NOTICE = (
    "Answer check settings changed on disk. Restart SLM or Save them in Settings "
    "to apply the whole choice."
)


def remember_settings(engine, retrieval, root=None):
    root = root or getattr(engine, "_answer_check_root", None)
    values = answer_check_state.snapshot(retrieval)
    engine._answer_check_settings = values
    judge = getattr(engine, "_sufficiency_judge", None)
    if judge is not None:
        judge._answer_check_settings = values
    if root is not None:
        engine._answer_check_root = root
        engine._answer_check_file = _fingerprint(root)
        if judge is not None:
            judge._answer_check_root = root


_UNSET = object()


def changed_on_disk(engine, judge=_UNSET) -> bool:
    child = getattr(engine, "_retrieval_engine", None)
    if child is not None:
        engine = child
    root = getattr(engine, "_answer_check_root", None)
    if judge is _UNSET:
        judge = getattr(engine, "_sufficiency_judge", None)
    baseline = getattr(judge, "_answer_check_settings", None)
    if baseline is None:
        baseline = getattr(engine, "_answer_check_settings", None)
    if not isinstance(root, (str, Path)) or baseline is None:
        return False  # direct retrieval engines without a settings store
    if _fingerprint(root) != getattr(engine, "_answer_check_file", None):
        return True
    stored, damaged = answer_check_state._load(root)
    if damaged:
        return True
    if stored is None:
        return False
    effective = {**baseline, **stored}
    return effective != baseline


def _fingerprint(root):
    try:
        stamp = answer_check_state.state_path(root).stat()
        return stamp.st_mtime_ns, stamp.st_size
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"
