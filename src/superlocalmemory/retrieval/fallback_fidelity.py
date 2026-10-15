# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bounded source admission for lexical answers outside the full pipeline."""

from __future__ import annotations

import logging
import threading
import time

from superlocalmemory.retrieval.source_fidelity_flags import flag_unverified
from superlocalmemory.storage.models import AtomicFact, RecallResponse, RetrievalResult

logger = logging.getLogger(__name__)
FALLBACK_FIDELITY_BUDGET_S = 0.5
_CAPACITY = threading.BoundedSemaphore(2)
_LOCK = threading.Lock()
_WORKERS: set[threading.Thread] = set()


def join_fallback_checks(timeout_s: float = 1.0) -> bool:
    """Join owned read workers within one deadline; report any remaining work."""
    deadline = time.monotonic() + timeout_s
    with _LOCK:
        workers = list(_WORKERS)
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    with _LOCK:
        _WORKERS.difference_update(w for w in workers if not w.is_alive())
        return not _WORKERS


def _check(rows: list[dict], db, profile_id: str, close_db: bool):
    """The worker owns a transferred read connection until its final read."""
    try:
        response = RecallResponse()
        candidates = [RetrievalResult(fact=AtomicFact(
            fact_id=row["fact_id"], memory_id=row["memory_id"],
            profile_id=row["profile_id"], content=row["content"],
        )) for row in rows]
        kept = flag_unverified(candidates, db, profile_id, response=response)
        ids = {r.fact.fact_id for r in kept}
        return [row for row in rows if row["fact_id"] in ids], response
    finally:
        if close_db:
            db.close()


def admit_fallback(
    rows: list[dict], db, profile_id: str, *,
    budget_s: float = FALLBACK_FIDELITY_BUDGET_S, close_db: bool = False,
) -> tuple[list[dict], RecallResponse]:
    """Run the full recall guard; a stalled check never admits unchecked rows.

    A timed-out read may finish in the background, but its private response
    cannot mutate the empty answer already returned. Connections transferred
    with ``close_db`` are closed by that worker, including after a timeout.
    """
    status = "degraded" if callable(getattr(db, "execute", None)) else "unavailable"
    refused = ([], RecallResponse(source_fidelity_status=status))
    if budget_s <= 0 or not _CAPACITY.acquire(blocking=False):
        if close_db:
            db.close()
        return refused
    done = threading.Event()
    answer = []

    def run():
        try:
            answer.append(_check(rows, db, profile_id, close_db))
        except Exception as exc:  # noqa: BLE001 - fail closed
            logger.warning("Fallback source admission failed (%s)", type(exc).__name__)
        finally:
            _CAPACITY.release()
            done.set()

    worker = threading.Thread(target=run, name="slm-fallback-fidelity", daemon=True)
    with _LOCK:
        _WORKERS.difference_update(w for w in list(_WORKERS) if not w.is_alive())
        _WORKERS.add(worker)
        try:
            worker.start()
        except RuntimeError:
            _WORKERS.discard(worker)
            _CAPACITY.release()
            if close_db:
                db.close()
            logger.warning("Fallback source admission worker unavailable")
            return refused
    if done.wait(budget_s):
        worker.join()
        return answer[0] if answer else refused
    logger.warning("Fallback source admission timed out; unchecked derivatives withheld")
    return refused
