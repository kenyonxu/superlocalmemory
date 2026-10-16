# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``GET /api/learning/readiness`` — what the learning loop has actually done.

Backs the dashboard's Learning card: daemon liveness and
learning evidence are separate states, and this endpoint reports only the
second, from ``learning.learning_state``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from .helpers import MEMORY_DIR, get_active_profile

logger = logging.getLogger("superlocalmemory.routes.learning_readiness")
router = APIRouter()


@router.get("/api/learning/readiness")
def learning_readiness(request: Request) -> dict:
    """Evidence-backed learning state for the active profile."""
    from superlocalmemory.access.rbac import Permission
    from superlocalmemory.learning.learning_state import (
        learning_readiness as _readiness,
    )
    from superlocalmemory.server.rbac_enforce import require_permission

    profile = get_active_profile()
    require_permission(request, Permission.READ, profile=profile)
    try:
        data = _readiness(MEMORY_DIR / "learning.db", MEMORY_DIR / "memory.db",
                          profile)
    except Exception:  # pragma: no cover — the reader never raises
        logger.exception("learning readiness failed")
        return {"available": False, "error": "Internal server error"}
    return {"available": True, "active_profile": profile, **data}
