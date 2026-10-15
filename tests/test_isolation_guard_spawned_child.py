# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A process spawned by a test keeps the parent's live-root guard.

``multiprocessing`` "spawn" children import the isolation plugin again after the
parent has pointed SLM_DATA_DIR at the test's tmp root. The child used to
recompute its protected roots from that environment, so it protected the test's
own tmp root and refused every write the test asked of it (the cross-process
vector-store test failed on every run). The child must allow the test's tmp root
and still refuse the account's real store.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path

from tests._isolation_plugin import _LIVE_DATA_ROOTS


def _try_connect(db_path: str, queue) -> None:
    try:
        sqlite3.connect(db_path).close()
        queue.put("ok")
    except PermissionError as exc:
        queue.put(f"refused: {exc}")


def _in_child(db_path: Path) -> str:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_try_connect, args=(str(db_path), queue))
    proc.start()
    try:
        return queue.get(timeout=30)
    finally:
        proc.join(timeout=30)


def test_a_spawned_child_may_write_the_tests_tmp_root(tmp_path: Path) -> None:
    assert _in_child(tmp_path / "child.db") == "ok"


def test_a_spawned_child_is_still_refused_the_real_store() -> None:
    real = Path.home()  # pytest HOME; the account root comes from the password db
    account_roots = [
        r for r in _LIVE_DATA_ROOTS
        if r.name == ".superlocalmemory" and real not in r.parents
    ]
    assert account_roots, "the guard must always protect the account's own store"
    verdict = _in_child(account_roots[0] / "memory.db")
    assert verdict.startswith("refused"), verdict


def _report_data_root(queue) -> None:
    import os
    queue.put(os.environ.get("SLM_DATA_DIR"))


def test_a_spawned_child_keeps_the_tests_data_root(tmp_path: Path) -> None:
    """A fresh root per child let two children both win a cross-process lock."""
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_report_data_root, args=(queue,))
    proc.start()
    try:
        assert queue.get(timeout=30) == str(tmp_path)
    finally:
        proc.join(timeout=30)
