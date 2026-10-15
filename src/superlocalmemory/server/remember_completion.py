"""Explicit synchronous saves complete the durable canonical operation."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException


def complete_remember(engine, payload: dict, profile_id: str) -> dict:
    """Materialize after admission commits, using the shared operation lock.

    An accepted admission has no canonical operation yet. Report that honestly;
    callers can retry its idempotency key rather than submitting another save.
    """
    from superlocalmemory.core.engine_ingestion import build_engine_ingestion_command
    from superlocalmemory.core.ingestion_command import IngestionState

    if payload.get('status') == 'accepted':
        return payload
    result = build_engine_ingestion_command(engine, profile_id=profile_id).materialize(
        payload['operation_id'])
    if result.state is not IngestionState.COMPLETE:
        raise HTTPException(503, detail={
            'code': 'MATERIALIZATION_INCOMPLETE',
            'message': 'Memory saved, but canonical enrichment did not complete.',
            'operation_id': result.operation_id,
            'materialization_state': result.state.value,
        })
    return {**payload, 'fact_ids': list(result.fact_ids), 'status': 'complete',
            'materialization_state': result.state.value}


async def wait_for_completion(engine, payload: dict, profile_id: str,
                              timeout_seconds: float = 290.0) -> dict:
    """Bound the request wait; timed out work remains durable and may finish."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(complete_remember, engine, payload, profile_id),
            timeout=timeout_seconds)
    except TimeoutError as exc:
        raise HTTPException(503, detail={
            'code': 'MATERIALIZATION_TIMEOUT',
            'message': 'Memory saved, but enrichment is still running. Retry the same command.',
            'operation_id': payload.get('operation_id'),
        }) from exc
