# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Recall Lab shares the agent recall deadline and retained transition leases."""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from superlocalmemory.server.recall_core import RecallCall, run_recall
from superlocalmemory.server.time_filter_error import checked_window_or_400


def install(router):
    """Replace the legacy trace route before mounting the existing V3 router."""
    if any(getattr(route, "endpoint", None) is recall_trace for route in router.routes):
        return
    router.routes[:] = [
        route for route in router.routes if getattr(route, "path", "") != "/api/v3/recall/trace"
    ]
    router.add_api_route("/recall/trace", recall_trace, methods=["POST"])


def _arguments(body):
    if not isinstance(body, dict) or not isinstance(body.get("query", ""), str):
        return JSONResponse({"error": "invalid_query"}, status_code=400)
    limit = body.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        return JSONResponse({"error": "invalid_limit"}, status_code=400)
    window = checked_window_or_400(body.get("window", "") or "")
    if isinstance(window, JSONResponse):
        return window
    values = {"query": body.get("query", ""), "limit": limit, "window": window}
    from superlocalmemory.retrieval.temporal_utils import normalize_as_of

    for key in ("as_of", "known_as_of", "valid_at"):
        raw = body.get(key, "") or ""
        if not isinstance(raw, str):
            return JSONResponse({"error": "invalid_" + key}, status_code=400)
        normalized = normalize_as_of(raw.strip()) if raw.strip() else ""
        if raw.strip() and normalized is None:
            return JSONResponse({"error": "invalid_" + key, "raw": raw}, status_code=400)
        values[key] = normalized
    unknown = body.get("include_unknown", False)
    if not isinstance(unknown, bool):
        return JSONResponse({"error": "invalid_include_unknown"}, status_code=400)
    return {**values, "include_unknown": unknown}


async def recall_trace(request: Request):
    from superlocalmemory.server.routes import v3_api

    profile = v3_api._resolve_profile(request)  # READ, before any store access
    try:
        values = _arguments(await request.json())
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if isinstance(values, JSONResponse):
        return values
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return JSONResponse(
            {
                "query": values["query"],
                "results": [],
                "result_count": 0,
                "answerability": "unjudged",
                "answer_check_status": "warming",
                "no_confident_match": False,
                "answer_check_note": "Memory engine is still starting; recall has not run.",
            },
            status_code=503,
        )
    call = RecallCall(
        **values,
        profile_id=profile,
        session_id=v3_api._signal_session_id(),
        agent_id="",
        fast=False,
        origin="dashboard",
        trace=True,
    )
    from superlocalmemory.server.recall_core import as_http

    return as_http(await run_recall(engine, call, app_state=request.app.state))


def envelope(engine, call, response):
    from superlocalmemory.core.kind_query import engine_display_min_confidence
    from superlocalmemory.server.recall_serializer import (
        recall_response_metadata,
        serialize_recall_response,
    )
    from superlocalmemory.server.routes import v3_api
    from superlocalmemory.server.routes.answer_check_history import answer_check_block

    results, no_match = serialize_recall_response(
        response,
        limit=call.limit,
        per_fact_max=300,
        total_max=max(300, call.limit * 300),
        display_min_confidence=engine_display_min_confidence(engine),
        include_evidence=True,
    )
    try:
        v3_api._record_learning_signals(call.query, results)
    except Exception:
        logging.getLogger(__name__).warning("Recall Lab learning signal could not be recorded")
    return {
        "query": call.query,
        "query_type": response.query_type,
        "result_count": len(results),
        "results": results,
        "synthesis": "",
        "retrieval_time_ms": response.retrieval_time_ms,
        "no_confident_match": no_match,
        **recall_response_metadata(response),
        "answer_check": answer_check_block(response),
        "query_evidence": dict(getattr(response, "query_evidence", {}) or {}),
    }
