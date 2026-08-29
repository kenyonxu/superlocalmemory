"""Journal-mode policy contract (superlocalmemory.storage.journal_policy).

The 2026-08-13/29 gateway freezes came from hard-coded WAL: its close path
can block on another process's reader pin while holding SQLite's global VFS
mutex. Every store must now go through apply_journal_mode so the mode is
centrally switchable (default DELETE) — no connection may force WAL on its
own.
"""

import os
import sqlite3
from unittest import mock

import pytest

from superlocalmemory.storage.journal_policy import (
    DEFAULT_JOURNAL_MODE,
    apply_journal_mode,
    resolve_journal_mode,
)


def test_default_mode_is_delete():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLM_JOURNAL_MODE", None)
        assert resolve_journal_mode() == DEFAULT_JOURNAL_MODE == "delete"


def test_env_override_wins():
    with mock.patch.dict(os.environ, {"SLM_JOURNAL_MODE": "WAL"}):
        assert resolve_journal_mode() == "wal"


def test_apply_yields_configured_mode(tmp_path):
    db = tmp_path / "policy.db"
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLM_JOURNAL_MODE", None)
        conn = sqlite3.connect(str(db))
        try:
            assert apply_journal_mode(conn) == "delete"
        finally:
            conn.close()


def test_apply_honors_wal_override(tmp_path):
    db = tmp_path / "policy_wal.db"
    with mock.patch.dict(os.environ, {"SLM_JOURNAL_MODE": "wal"}):
        conn = sqlite3.connect(str(db))
        try:
            assert apply_journal_mode(conn) == "wal"
        finally:
            conn.close()


def test_no_source_file_forces_wal():
    """Invariant: no module under src/ executes a bare journal_mode=WAL pragma."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        ["grep", "-rn", 'PRAGMA journal_mode=WAL"', "--include=*.py", str(root / "src")],
        capture_output=True, text=True,
    )
    hits = [l for l in out.stdout.splitlines() if "journal_policy.py" not in l]
    assert not hits, f"hard-coded WAL pragma remains: {hits}"
