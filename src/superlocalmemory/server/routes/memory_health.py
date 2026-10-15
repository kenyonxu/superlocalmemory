# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The same memory-health actions for dashboard and CLI."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from superlocalmemory.access.rbac import Permission
from superlocalmemory.core.enrichment import EnrichmentRunner
from superlocalmemory.core.metadata_health import report
from superlocalmemory.server import write_identity
from superlocalmemory.server.rbac_enforce import require_permission

router = APIRouter(prefix='/api/memory-health', tags=['memory-health'])
HEALTH_CACHE_S = 10.0


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['pause', 'resume', 'undo', 'repair']
    run_id: str = Field('', max_length=64, pattern=r'^[a-zA-Z0-9_-]*$')
    repair: Literal['refile-hidden', 'qjl-sketches', 'entity-links', 'repair-saves'] | None = None
    include_undone: bool = False  # `slm enrich fix --include-undone`
    confirm: bool = False  # required by a repair that cannot be undone (repair-saves)


def _engine(request):
    engine = getattr(request.app.state, 'engine', None)
    if engine is None or getattr(engine, '_db', None) is None:
        raise HTTPException(503, 'Your memories are still starting.')
    return engine


@router.get('')
def get_health(request: Request):
    # Store-wide maintenance counts and undo are for its owner/admin only.
    require_permission(request, Permission.MANAGE)
    engine = _engine(request)
    from superlocalmemory.server.view_cache import cached

    # Every open dashboard polls this; one scan answers them all for a few seconds.
    key = (str(getattr(engine._db, 'db_path', id(engine._db))),
           str(getattr(engine, 'profile_id', '')))
    return cached('memory-health', key, HEALTH_CACHE_S, lambda: report(engine._db))


@router.get('/stubs')
def get_stubs(request: Request, limit: int = 50):
    require_permission(request, Permission.MANAGE)
    engine = _engine(request)
    return {'facts': [dict(r) for r in engine._db.execute(
        'SELECT fact_id,content FROM atomic_facts WHERE profile_id=? AND non_answer=1 LIMIT ?',
        (engine.profile_id, max(1, min(200, limit))))]}


@router.post('/action')
def change_health(body: Action, request: Request):
    write_identity.require_write_actor(
        request, getattr(request.app.state, 'daemon_descriptor', None), actor_kind='memory-health')
    require_permission(request, Permission.MANAGE)
    engine = _engine(request)
    from superlocalmemory.server.view_cache import drop

    drop('memory-health')  # the person changed it: the next read shows the change
    runner = EnrichmentRunner(engine._db, idle=lambda: False)
    try:
        if body.action == 'repair':
            from superlocalmemory.core.health_repairs import repair_action
            if not body.repair:
                raise ValueError('Choose the repair action.')
            return repair_action(engine, body.repair, dry_run=False,
                                 include_undone=body.include_undone, confirmed=body.confirm)
        if not body.run_id:
            raise ValueError('An improvement run is required.')
        if body.action == 'undo':
            from superlocalmemory.core.health_repairs import undo_action
            return undo_action(engine, body.run_id)
        return getattr(runner, body.action)(body.run_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
