# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Local background calibration; no hosted judge or third-party text egress."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from superlocalmemory.retrieval.judge_calibration_file import FILE_NAME, SCHEMA, parse_entries

_WRITE_LOCK = threading.Lock()


def write_calibration(root: Path, entry: dict, semantic: dict | None = None, *,
                      expected_stamp: str | None = None) -> None:
    """Validate then replace; failed/partial jobs never disturb previous calibration."""
    path = root / FILE_NAME
    with _WRITE_LOCK:
        from superlocalmemory.core.calibration_guard import file_stamp
        if expected_stamp is not None and file_stamp(root) != expected_stamp:
            raise ValueError("Calibration file changed during measurement; newer settings kept")
        previous = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists()
            else {"schema": SCHEMA, "entries": []}
        )
        entries = [
            e
            for e in previous["entries"]
            if (e["backend"], e["recipe_id"]) != (entry["backend"], entry["recipe_id"])
        ]
        payload = {**previous, "schema": SCHEMA, "entries": [*entries, entry]}
        if semantic is not None:
            old = [
                e for e in payload.get("semantic", []) if e["signature"] != semantic["signature"]
            ]
            payload["semantic"] = [*old, semantic]
        parse_entries(payload)
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=root, prefix=".answer-calibration-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(payload, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


class CalibrationJob:
    """One bounded job per daemon. Progress includes operational skips separately."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict = {"state": "idle", "done": 0, "total": 0}
        self._thread: threading.Thread | None = None
        from superlocalmemory.core.calibration_control import CancelToken

        self._stop = CancelToken()
        self._shutdown = threading.Event()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _progress(self, **values) -> None:
        with self._lock:
            self._status = {**self._status, **values}

    def start(self, engine: Any, *, model: str = "llama3.2:latest", samples: int = 32,
              current_engine=None, lease=None) -> dict:
        if self._shutdown.is_set():
            raise ValueError("The calibration service is stopping")
        from superlocalmemory.core.calibration_control import require_local

        require_local(engine)
        if model not in {"llama3.2:latest", "gemma3:4b"}:
            raise ValueError("Use an installed GPU model: llama3.2:latest or gemma3:4b")
        if not 32 <= samples <= 64:
            raise ValueError("Calibration needs 32-64 sampled facts")
        judge = getattr(getattr(engine, "_retrieval_engine", None), "_sufficiency_judge", None)
        if getattr(judge, "backend", None) != "laya" or not judge.ready:
            raise ValueError("The on-device answer check must be warm before calibration")
        with self._lock:
            if (self._status["state"] in {"running", "warming", "cancelling"}
                    or (self._thread is not None and self._thread.is_alive())):
                raise ValueError("Calibration is already running")
            from superlocalmemory.core.calibration_control import CancelToken

            self._stop = CancelToken()
            self._stop.bind(engine, current_engine, lease, self._progress)
            self._status = {
                "state": "running",
                "done": 0,
                "total": samples + samples // 2,
                "model": model,
                "skipped": 0,
                "detail": "Building local labels",
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(engine, judge, model, samples, self._stop),
                name="slm-answer-calibration",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        """Cancel only the active run; publication and cancellation share a lock."""
        with self._lock:
            if self._status["state"] in {"running", "warming", "cancelling"}:
                self._stop.set()
                self._status = {**self._status, "state": "cancelling",
                                "detail": "Cancelling calibration; previous settings kept"}
            return dict(self._status)

    def stop(self, timeout: float = 1.0) -> None:
        with self._lock:
            self._shutdown.set()
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, engine, judge, model, samples, stop):
        from superlocalmemory.core.calibration_examples import build_examples
        from superlocalmemory.core.calibration_measure import measure_examples

        try:
            from superlocalmemory.core.recall_gate import background_work

            with background_work(preempt_requested=stop.is_set):
                examples = build_examples(engine, model, samples, stop)
                measured = measure_examples(engine, judge, examples, stop, self._progress)
            if stop.is_set():
                raise ValueError("Calibration cancelled")
            entry = _entry(judge, measured)
            semantic = measured.get("semantic")
            if semantic is not None and not semantic.get("usable", True):
                semantic = None  # retain a working floor when the holdout is poor
            with stop.operation(), self._lock, getattr(judge, "_lock", nullcontext()):
                if stop.is_set():
                    raise ValueError("Calibration cancelled")
                from superlocalmemory.core.calibration_guard import require_comparison
                require_comparison(measured, judge, engine)
                write_calibration(engine._config.base_dir, entry, semantic,
                                  expected_stamp=measured["baseline"]["file_stamp"])
                self._status = {**self._status, "state": "complete", "detail": "Calibration saved"}
            from superlocalmemory.retrieval.embedding_calibration import apply_to
            from superlocalmemory.retrieval.laya_set_recipe import refresh

            refresh(judge)
            apply_to(engine._config, engine._embedder)
            from superlocalmemory.core.answer_check_memo import clear

            clear(judge)
            detail = "Calibration saved"
            if measured.get("embedding_status") != "measured":
                detail = "Answer check calibrated; previous embedding floor kept (" + measured.get(
                    "embedding_status", "unmeasured") + ")"
            self._progress(state="complete", detail=detail, metrics=measured)
        except Exception as exc:
            self._progress(state="cancelled" if stop.cancel_requested else "failed",
                           detail=str(exc)[:200])
        finally:
            self._progress(finished_at=time.time())


def _entry(judge, measured):
    return {
        "backend": "laya", "recipe_id": "sufficiency-set-v2",
        "threshold": measured["judge"]["threshold"],
        "answered": measured["answered"], "unanswered": measured["unanswered"],
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "model": judge._model, "top_k": judge.top_k,
        "metrics": measured["judge"], "labels": "local_model_silver",
    }
