# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""An offline restore as one unit: hold the store, change everything or nothing.

``backup_bundle.restore_point`` restores the databases atomically (the backup
coordinator keeps pre-restore copies and rolls back), then merges settings and
copies media originals. Those later steps can fail after the databases have
changed. This module wraps the whole thing:

* The writer lease (``memory.db.writer.lock``, the file the write coordinator
  claims) is held for the whole run, with this process recorded as holder, so
  a daemon started by a hook or a CLI call cannot claim the store and write
  part-way through. ``restore_in_progress`` lets daemon start-up refuse early,
  before migrations run.
* Before writing, everything the restore may change is copied as plain files
  (``backup_rollback``). On any failure those files are put back, without
  running any restore code again. Only if that rollback also fails is the
  store left for ``slm backup restore-safety-copy``, which restores the safety
  point taken after the stop.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from superlocalmemory.server import recovery_marker
from superlocalmemory.storage._durable_json import read_json

OUTCOME_NAME = "backup-recovery-outcome.json"
IN_PROGRESS = ("queued", "stopping", "restoring", "restarting")
RECOVER_COMMAND = "slm backup restore-safety-copy"
_LEASE_WAIT_S = 15.0


class StoreBusy(RuntimeError):
    """Another process holds the store's writer lease; nothing was changed."""


class RolledBack(RuntimeError):
    """The restore failed and the safety copy was put back."""


class RollbackFailed(RuntimeError):
    """The restore failed and putting the safety copy back failed too."""


def _lease_path(root: Path) -> Path:
    from superlocalmemory.storage._restore_boot import _lease_path as lease_for

    return lease_for(Path(root) / "memory.db")


@contextmanager
def writer_lease(root: Path, wait_s: float = _LEASE_WAIT_S) -> Iterator[None]:
    """Hold the store's writer lease, recorded as this process, for the block."""
    from superlocalmemory.core.file_lock import LockHeldError, exclusive_lock

    path = _lease_path(root)
    deadline = time.monotonic() + wait_s
    while True:
        lock = exclusive_lock(path, timeout_s=0.0)
        try:
            fd = lock.__enter__()
            break
        except LockHeldError:
            if time.monotonic() >= deadline:
                raise StoreBusy("Another SuperLocalMemory process has the store open") from None
            time.sleep(0.2)
    try:
        record = {"owner_id": "backup-restore", "pid": os.getpid(),
                  "database": str(Path(root) / "memory.db"),
                  "claimed_at_ms": int(time.time() * 1000)}
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(record, sort_keys=True).encode("utf-8") + b"\n")
        yield
    finally:
        try:  # no record naming a live process once the lease is free
            os.ftruncate(fd, 0)
        finally:
            lock.__exit__(None, None, None)


RECOVERY_NEEDED = recovery_marker.NAME
CLEAR_COMMAND = recovery_marker.CLEAR_COMMAND


def mark_recovery_needed(root: Path, reason: str, safety_id: str | None,
                         kept_copy: str | None = None) -> None:
    """Record that the stores may be mixed; start-up refuses until it is cleared."""
    recovery_marker.needs_recovery(root, reason, safety_id, kept_copy)


def clear_recovery_needed(root: Path) -> bool:
    """Remove the marker and any pre-restore copy it kept (owner's explicit choice)."""
    return recovery_marker.clear(root)


def restore_in_progress(root: Path) -> str | None:
    """Why a daemon must not start now, or None. Never raises."""
    found = start_refusal(root)
    return found[1] if found else None


def start_refusal(root: Path) -> tuple[str, str] | None:
    """``(kind, message)`` why nothing may start, or None. Never raises.

    ``recovery_marker.RUNNING``: a live restore; waiting is the answer.
    ``recovery_marker.RECOVERY``: the stores may be mixed; run the command.
    """
    try:
        found = recovery_marker.state(root)
    except Exception:  # noqa: BLE001
        found = None
    if found:
        return found
    try:
        status = read_json(Path(root) / OUTCOME_NAME) or {}
    except Exception:  # noqa: BLE001 - an unreadable note never blocks start-up
        return None
    if status.get("state") in ("stopping", "restoring") and recovery_marker.writer_alive(
            status, require_identity=True):
        return recovery_marker.RUNNING, ("A dashboard restore is replacing the memory store; "
                                         "start again when it finishes.")
    return None


def restore_atomically(root: Path, point_id: str, *, safety_id: str | None = None) -> None:
    """Restore ``point_id`` as one unit, or leave every covered file as it was.

    ``recovery-needed.json`` is written before anything is touched and removed
    only after success or a clean rollback, so a process that dies at any
    point leaves every start refused (``recovery_marker``). The rollback is a
    file copy taken just before the restore (``backup_rollback``); it runs
    none of the restore code. When even the rollback fails, the copy is kept
    and named in the marker.
    """
    from superlocalmemory.server import backup_bundle
    from superlocalmemory.server.backup_rollback import PreRestoreCopy

    root = Path(root)
    previous = recovery_marker.begin(root, safety_id)
    try:
        copy = PreRestoreCopy.take(root)
    except Exception:
        recovery_marker.end_clean(root, previous)  # nothing was written yet
        raise
    try:
        backup_bundle.restore_point(root, point_id)
    except Exception as exc:
        cause = exc
    else:
        copy.discard()
        recovery_marker.end_clean(root, None)
        return
    try:
        copy.roll_back()
    except Exception as exc:
        recovery_marker.needs_recovery(root, "the restore and its rollback both failed",
                                       safety_id, str(copy.folder))
        failed = RollbackFailed(str(exc))
        failed.kept_copy = str(copy.folder)
        raise failed from cause
    copy.discard()
    recovery_marker.end_clean(root, previous)
    raise RolledBack(str(cause)) from cause


def recover_safety_copy(root: Path, safety_id: str) -> dict:
    """Put a safety copy back with the service stopped (the CLI path)."""
    from superlocalmemory.server.backup_bundle import create_point, verify_point
    from superlocalmemory.server.backup_rollback import check_free_space, sweep_stale
    from superlocalmemory.storage._durable_json import write_json_atomic

    verify_point(root, safety_id)
    with writer_lease(root, wait_s=0.0):
        sweep_stale(root)
        check_free_space(root)
        # The point taken first must never prune the copy it is about to restore.
        before = create_point(root, keep=frozenset({safety_id}))["id"]
        kept = recovery_marker.kept_copies(recovery_marker.read(root))
        restore_atomically(root, safety_id, safety_id=before)
        recovery_marker.remove_copies(root, kept)  # the stores are whole again
        write_json_atomic(Path(root) / OUTCOME_NAME, {
            "state": "recovered", "id": safety_id, "safety_id": before,
            "message": "The safety copy was put back. Start the service with `slm serve start`."})
    return {"restored": safety_id, "safety_id": before}


LOG_NAME = "backup-recovery.log"
_STALE_S = 15 * 60


def log_path(root: Path) -> Path:
    return Path(root) / "logs" / LOG_NAME


def _dead(status: dict, mtime: float | None) -> bool:
    pid = status.get("worker_pid")
    if pid is not None:
        return not recovery_marker.writer_alive(status, require_identity=True)
    return mtime is not None and time.time() - mtime > _STALE_S


def settle(status: dict, root: Path, mtime: float | None = None) -> dict:
    """Turn an in-progress status whose worker has died into an honest failure.

    The worker holds the writer lease through the operating system, so its
    death already released the store; this releases the queue (a dead
    "restoring" no longer answers every later restore with 409) and says what
    the operator should do, pointing at the worker's log.
    """
    if status.get("state") not in IN_PROGRESS or not _dead(status, mtime):
        return status
    stage = status.get("state")
    details = f"Details are in {log_path(root)}."
    if stage == "restoring":
        if recovery_marker.read(root) is None:  # normally written before the first write
            mark_recovery_needed(root, "the restore worker ended while restoring",
                                 status.get("safety_id"))
        message = ("The recovery stopped part-way because its process ended, so the stores "
                   "may be partly restored. Stop the service (`slm serve stop`) and run "
                   f"`{RECOVER_COMMAND}` to put the safety copy back. {details}")
    elif stage == "restarting":
        message = ("The backup was restored but the restart may not have finished. "
                   f"If the dashboard does not respond, run `slm serve start`. {details}")
    else:
        message = ("Recovery stopped before restoring anything, so nothing was changed. "
                   f"If the service is not running, start it with `slm serve start`. {details}")
    status.update({"state": "failed", "message": message, "interrupted_at": stage})
    return status
