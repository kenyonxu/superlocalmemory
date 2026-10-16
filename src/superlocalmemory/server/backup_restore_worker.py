"""Detached restore: stop the owning daemon, restore offline, restart it.

Order, and why:

1. Verify the point, record this worker's pid (a daemon start refuses while
   it is alive and restoring, see ``backup_restore_txn.restore_in_progress``).
2. Stop the daemon and wait for its process to exit.
3. Take the store's writer lease and hold it until the restore is finished,
   so nothing can claim the store in the meantime.
4. Take a safety point (for ``slm backup restore-safety-copy``), then restore
   as one unit: on any failure the plain pre-restore file copy is put back
   (``restore_atomically``).
5. Release the lease and start the daemon. It stays stopped only when the
   rollback itself failed; the outcome then names the command that puts the
   safety copy back.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from superlocalmemory.server import recovery_marker
from superlocalmemory.server.backup_bundle import create_point, verify_point
from superlocalmemory.server.backup_restore_txn import (
    RECOVER_COMMAND,
    RollbackFailed,
    RolledBack,
    restore_atomically,
    writer_lease,
)
from superlocalmemory.server.backup_rollback import NotEnoughSpace, check_free_space, sweep_stale
from superlocalmemory.storage._durable_json import write_json_atomic

logger = logging.getLogger(__name__)


def control(root: Path, action: str) -> None:
    env = {**os.environ, "SLM_DATA_DIR": str(root)}
    result = subprocess.run(
        [sys.executable, "-m", "superlocalmemory.cli.main", "serve", action],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Daemon lifecycle action failed")


def wait_stopped(pid: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        from superlocalmemory.core.platform_utils import is_pid_alive
        if not is_pid_alive(pid):
            return
        time.sleep(0.2)
    raise RuntimeError("Daemon did not stop; restore refused")


class _Outcome:
    """The status file the dashboard polls; every write names this worker."""

    def __init__(self, root: Path, point_id: str) -> None:
        self.path = Path(root) / "backup-recovery-outcome.json"
        self.base = {"id": point_id, "worker_pid": os.getpid(),
                     "worker_identity": recovery_marker.identity(os.getpid())}
        self.safety_id: str | None = None

    def write(self, state: str, message: str | None = None) -> None:
        record = {**self.base, "state": state, "safety_id": self.safety_id}
        if message:
            record["message"] = message
        write_json_atomic(self.path, record)


def _restore(root: Path, point_id: str, status: _Outcome) -> None:
    with writer_lease(root):
        status.safety_id = create_point(root)["id"]
        status.write("restoring")
        restore_atomically(root, point_id, safety_id=status.safety_id)


def run(root: Path, point_id: str, pid: int) -> None:
    status = _Outcome(root, point_id)
    stopped = succeeded = False
    try:
        verify_point(root, point_id)
        sweep_stale(root)
        check_free_space(root)
        status.write("stopping")
        control(root, "stop")
        wait_stopped(pid)
        stopped = True
        _restore(root, point_id, status)
    except RollbackFailed as exc:
        logger.exception("Backup recovery failed and the safety copy could not be put back")
        kept = getattr(exc, "kept_copy", None)
        status.write("failed", (
            "Recovery failed and the previous state could not be put back automatically. "
            "The service stays stopped so nothing writes to a mixed store. Run "
            f"`{RECOVER_COMMAND}` to put the safety copy back, then `slm serve start`."
            + (f" The stores as they were before the restore are kept in {kept}." if kept
               else "")))
        return
    except RolledBack:
        logger.exception("Backup recovery failed; the state before it was put back")
        status.write("failed", "Recovery could not finish, so nothing was changed: the "
                               "stores, settings and files are as they were before.")
    except NotEnoughSpace as exc:
        logger.error("Backup recovery refused: %s", exc)
        status.write("failed", str(exc))
    except Exception as exc:
        logger.exception("Backup recovery failed before anything was restored")
        status.write("failed", _not_started_message(exc))
    else:
        succeeded = True
        status.write("restarting")
    if not stopped:
        return
    try:
        control(root, "start")
    except Exception:
        logger.exception("Restart after recovery did not finish")
        status.write("failed", "The service did not restart. Run `slm serve start`.")
        return
    if succeeded:
        status.write("complete", "Recovery backup restored and the daemon restarted.")


def _not_started_message(exc: BaseException) -> str:
    import errno

    while exc is not None:
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            return ("The disk is full, so the restore could not start and nothing was "
                    "changed. Free some space and retry.")
        exc = exc.__cause__ or exc.__context__
    return ("Recovery could not start, so nothing was changed. "
            "Retry when other activity has finished.")


def _log_to_file(root: Path) -> None:
    """Keep the worker's reasons: it runs detached, with nobody reading stderr."""
    from superlocalmemory.server.backup_restore_txn import log_path

    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


if __name__ == "__main__":
    _log_to_file(Path(sys.argv[1]))
    run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
