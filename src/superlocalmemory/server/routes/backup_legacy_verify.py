"""Honest verification of pre-manifest database snapshots; no restore promise."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class LegacyBody(BaseModel):
    filename: str = Field(min_length=1, max_length=200)


@router.post("/verify-legacy")
def verify_legacy(request: Request, body: LegacyBody):
    from superlocalmemory.server.rbac_enforce import require_global_manage
    from superlocalmemory.server.routes.backup import _get_backup_manager
    from superlocalmemory.storage._snapshot_manifest import sha256_file, verify_snapshot

    require_global_manage(request)
    root = Path(_get_backup_manager().backup_dir).resolve()
    path = root / body.filename
    if path.name != body.filename or path.suffix != ".db" or path.is_symlink():
        raise HTTPException(422, "Choose a database snapshot from the backup history.")
    if path.resolve().parent != root or not path.is_file():
        raise HTTPException(404, "That backup is not available.")
    try:
        verify_snapshot(path, None)
    except Exception:
        raise HTTPException(
            409, "This database snapshot failed verification. Nothing was restored."
        ) from None
    return {
        "verified": True,
        "filename": body.filename,
        "sha256": sha256_file(path),
        "message": ("The database is readable. Older snapshots have no complete recovery manifest. "
                    "Create a recovery backup above for a verified multi-store restore."),
    }
