# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the Elastic License 2.0 - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Ingestion endpoint — accepts data from external adapters.

POST /ingest with {content, source_type, dedup_key, metadata}.
Deduplicates by source_type + dedup_key. Stores via MemoryEngine.
Admission control: max 10 concurrent ingestions (HTTP 429 on overflow).

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["ingestion"])

_MAX_CONCURRENT = 10
_active_count = 0
_active_lock = threading.Lock()


class IngestRequest(BaseModel):
    content: str
    source_type: str
    dedup_key: str
    metadata: dict = {}


@router.post("/ingest")
async def ingest(req: IngestRequest, request: Request):
    """Ingest content from an external adapter.

    Deduplicates by (source_type, dedup_key). Returns 429 if too many
    concurrent ingestions. Stores via the singleton MemoryEngine.
    """
    global _active_count

    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(503, detail="Engine not initialized")

    if not req.content:
        raise HTTPException(400, detail="content required")
    if not req.source_type:
        raise HTTPException(400, detail="source_type required")
    if not req.dedup_key:
        raise HTTPException(400, detail="dedup_key required")

    # Admission control
    with _active_lock:
        if _active_count >= _MAX_CONCURRENT:
            raise HTTPException(
                429,
                detail="Too many concurrent ingestions",
                headers={"Retry-After": "5"},
            )
        _active_count += 1

    try:
        # Dedup check
        conn = sqlite3.connect(str(engine._config.db_path))
        try:
            existing = conn.execute(
                "SELECT id FROM ingestion_log WHERE source_type=? AND dedup_key=?",
                (req.source_type, req.dedup_key),
            ).fetchone()
            if existing:
                return {"ingested": False, "reason": "already_ingested"}
        finally:
            conn.close()

        # Store via engine
        metadata = {**req.metadata, "source_type": req.source_type}
        fact_ids = engine.store(req.content, metadata=metadata)

        # Log to ingestion_log
        conn = sqlite3.connect(str(engine._config.db_path))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ingestion_log "
                "(source_type, dedup_key, fact_ids, metadata, status, ingested_at) "
                "VALUES (?, ?, ?, ?, 'ingested', ?)",
                (
                    req.source_type,
                    req.dedup_key,
                    json.dumps(fact_ids),
                    json.dumps(req.metadata),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {"ingested": True, "fact_ids": fact_ids}

    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
    finally:
        with _active_lock:
            _active_count -= 1
