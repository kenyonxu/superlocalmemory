# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Local calibration API, attached to the existing authorized answer-check router."""

from __future__ import annotations

import threading

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from superlocalmemory.core.answer_calibration import CalibrationJob

_LOCK = threading.Lock()


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field("llama3.2:latest", pattern="^(llama3\\.2:latest|gemma3:4b)$")
    samples: int = Field(32, ge=32, le=64)


def job_for(state):
    with _LOCK:
        job = getattr(state, "answer_calibration_job", None)
        if job is None:
            job = CalibrationJob()
            state.answer_calibration_job = job
        return job


def register(router, gate):
    @router.get("/calibration")
    def status(request: Request):
        return job_for(request.app.state).status()

    @router.post("/calibration")
    def start(body: CalibrationRequest, request: Request):
        gate(request)
        engine = getattr(request.app.state, "engine", None)
        if engine is None:
            return JSONResponse({"detail": "Memory engine is still warming"}, status_code=409)
        from superlocalmemory.server.profile_runtime import get_profile_runtime

        runtime = get_profile_runtime(request.app.state)
        try:
            return JSONResponse(
                job_for(request.app.state).start(engine, model=body.model, samples=body.samples,
                                            current_engine=lambda: request.app.state.engine,
                                            lease=runtime.operation_nowait),
                status_code=202,
            )
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    @router.post("/calibration/cancel")
    def cancel(request: Request):
        gate(request)
        return JSONResponse(job_for(request.app.state).cancel(), status_code=202)
