"""Dashboard recovery: verify before export or confirmed offline restore."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from superlocalmemory.server import backup_bundle as bundles
from superlocalmemory.server import recovery_marker
from superlocalmemory.server.backup_restore_txn import IN_PROGRESS, log_path, settle
from superlocalmemory.server.config_file import update_config
from superlocalmemory.storage._durable_json import read_json, write_json_atomic

from .backup_legacy_verify import router as legacy_router

router = APIRouter(prefix="/api/backup/recovery", tags=["backup-recovery"])
router.include_router(legacy_router)
logger = logging.getLogger(__name__)


class PointBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{16}$")
    confirm: str = ""


def _root():
    from superlocalmemory.server.routes.backup import _get_backup_manager

    return Path(_get_backup_manager().db_path).parent


def _gate(request):
    """Recovery reads and replaces every workspace's data: installation-wide MANAGE.

    MANAGE on the active workspace is not enough (PR 251 review, finding 1).
    The machine owner or an administrator of every workspace may act; the
    mutation gate's ``/api/backup`` rule (scope ``global``) refuses the rest
    before the handler runs, and this covers the read routes too.
    """
    from superlocalmemory.server.rbac_enforce import require_global_manage

    require_global_manage(request)


def _verify(point_id):
    try:
        return bundles.verify_point(_root(), point_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/list")
def recovery_list(request: Request):
    _gate(request)
    items = []
    for file in sorted((_root() / "backups").glob("backup_*/recovery.json"), reverse=True):
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
            items.append(
                {
                    "recovery_id": record["id"],
                    "created": record["created"],
                    "stores": record["stores"],
                    "scope": "Managed stores and settings",
                    "size_mb": sum(e["bytes"] for e in record["files"]) / 1024**2,
                    "status": "Verify before restore",
                }
            )
        except (OSError, ValueError, KeyError):
            logger.warning("A recovery record could not be read")
    from superlocalmemory.server.routes.backup import _get_backup_manager

    legacy = [
        dict(item, scope="Legacy memory database", status="Verify database only")
        for item in _get_backup_manager().list_backups()
        if item.get("type") == "memory"
    ]
    items.extend(legacy)
    items.sort(key=lambda item: item.get("created", ""), reverse=True)
    return {"backups": items, "count": len(items)}


@router.post("/create")
def recovery_create(request: Request):
    _gate(request)
    try:
        point = bundles.create_point(_root())
        return {"success": True, "filename": point["id"], "recovery_id": point["id"]}
    except Exception:
        logger.exception("Recovery snapshot failed")
        raise HTTPException(
            503, "Recovery backup could not be created. Retry after other writes finish."
        ) from None


@router.post("/verify")
def recovery_verify(request: Request, body: PointBody):
    _gate(request)
    return _verify(body.id)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, pattern=r"^[a-f0-9]{16}$")


def _export_target(root: Path, point_id: str | None) -> str:
    """The point to export: the one named, else the newest kept one.

    Only when no point exists yet is one created, so an export never evicts
    an older point from retention (PR 251 review, finding 7).
    """
    if point_id:
        return point_id
    from superlocalmemory.server.recovery_inventory import points

    kept = points(root)
    return kept[0]["id"] if kept else bundles.create_point(root)["id"]


@router.post("/export")
def recovery_export(request: Request, body: ExportBody | None = None):
    _gate(request)
    root = _root()
    try:
        point_id = _export_target(root, body.id if body else None)
        path = bundles.export_point(root, point_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        logger.exception("Recovery export failed")
        raise HTTPException(
            503, "The export could not be prepared. Check free disk space and retry."
        ) from None
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"slm-recovery-{point_id}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


def _outcome_path(root: Path) -> Path:
    return root / "backup-recovery-outcome.json"


def _settled(root: Path) -> dict:
    """The recovery status, with a dead worker's stuck state turned into a failure."""
    path = _outcome_path(root)
    current = read_json(path)
    if not current:
        return {"state": "idle"}
    mtime = path.stat().st_mtime
    if settle(dict(current), root, mtime).get("state") == current.get("state"):
        return current
    result: dict = {}

    def apply(status):  # re-checked under the lock: the worker may have moved on
        settle(status, root, mtime)
        result.update(status)

    update_config(path, apply)
    return result


@router.get("/status")
def recovery_status(request: Request):
    _gate(request)
    return _settled(_root())


def _spawn(root, point_id) -> int:
    """Start the detached worker; its output goes to logs/backup-recovery.log."""
    from superlocalmemory.core.platform_utils import popen_platform_kwargs
    from superlocalmemory.infra.private_files import create_private_file

    log = log_path(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        create_private_file(log)
    with open(log, "ab") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "superlocalmemory.server.backup_restore_worker",
                str(root),
                point_id,
                str(os.getpid()),
            ],
            env={**os.environ, "SLM_DATA_DIR": str(root)},
            stdout=output,
            stderr=subprocess.STDOUT,
            **popen_platform_kwargs(),
        )
    return process.pid


@router.post("/restore")
def recovery_restore(request: Request, body: PointBody):
    _gate(request)
    if body.confirm != "RESTORE":
        raise HTTPException(422, "Type RESTORE to confirm replacing your stores.")
    _verify(body.id)
    root = _root()
    from superlocalmemory.server.backup_rollback import NotEnoughSpace, check_free_space

    try:
        check_free_space(root)
    except NotEnoughSpace as exc:
        raise HTTPException(409, str(exc)) from None

    def reserve(status):
        if status.get("state") in IN_PROGRESS:
            raise HTTPException(409, "A recovery is already in progress.")
        status.clear()
        status.update({"state": "queued", "id": body.id})

    _settled(root)
    update_config(_outcome_path(root), reserve)
    try:
        worker = _spawn(root, body.id)
    except Exception:
        logger.exception("Recovery worker could not start")
        write_json_atomic(_outcome_path(root), {"state": "failed", "id": body.id})
        raise HTTPException(503, "Recovery could not start. Nothing was restored; retry.") from None

    def record(status):
        status.setdefault("worker_pid", worker)
        status.setdefault("worker_identity", recovery_marker.identity(worker))

    update_config(_outcome_path(root), record)
    return {
        "success": True,
        "state": "queued",
        "message": "Stopping, restoring and restarting. Reopen this page to see the outcome.",
    }
