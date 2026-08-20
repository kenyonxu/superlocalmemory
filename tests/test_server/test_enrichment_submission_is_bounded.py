# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A saturated enrichment pool must refuse work, not queue it.

The pool that makes a new memory searchable by meaning has two workers, and a
``ThreadPoolExecutor`` queues without limit. Bounding the pool's width therefore
does not bound what may be handed to it: a burst of concurrent writes all submit,
two run, and the rest sit in a queue after their callers have already been
answered — then wake up and write to the database while the next request holds
the write lock.

So the bound has to be applied at the point of submission. A permit is taken
before submitting and released by the worker when it has genuinely finished; a
caller that cannot get one is answered immediately with "findable by wording",
which is the behaviour that existed before inline enrichment and is the intended
degradation.

Asserting that the semaphore *exists* and has the right capacity does not test
any of this — an unused semaphore satisfies both, and did. These tests drive the
real write route, so saturation has to change the observable outcome.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import superlocalmemory.server.unified_daemon as ud
from superlocalmemory.server.unified_daemon import create_app
from superlocalmemory.storage.migrations import (
    M018_ingestion_operations,
    M032_write_coordinator_admission,
    M033_projection_transactions,
    M034_obligation_integrity,
    M042_correction_case_ledger,
)


@pytest.fixture
def client(engine_with_mock_deps):
    """The real write route, with the daemon-owned writer injected.

    ``TestClient`` does not run the lifespan, so the writer this route requires
    has to be attached by hand or every request is answered 503 long before it
    reaches anything worth testing.
    """
    from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime

    engine = engine_with_mock_deps
    with engine._db.raw_connection() as conn:
        M018_ingestion_operations.apply(conn)
        M032_write_coordinator_admission.apply(conn)
        M033_projection_transactions.apply(conn)
        M034_obligation_integrity.apply(conn)
        M042_correction_case_ledger.apply(conn)
    app = create_app()
    app.state.engine = engine
    runtime = CanonicalRememberRuntime.for_engine(engine)
    runtime.start()
    app.state.canonical_remember_runtime = runtime
    c = TestClient(app)
    c.headers["X-SLM-Daemon-Capability"] = app.state.daemon_descriptor.capability
    c.headers["X-SLM-Target-Instance"] = app.state.daemon_descriptor.instance_id
    try:
        yield c
    finally:
        runtime.stop()


@pytest.fixture
def executor_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Records whether the route reached the point of submitting work."""
    spy = MagicMock(name="enrichment_executor")
    monkeypatch.setattr(ud, "_enrichment_executor", spy)
    return spy


@pytest.fixture
def saturated() -> object:
    """Hold every permit for the duration of a test, then give them all back."""
    held = 0
    while ud._enrichment_semaphore.acquire(blocking=False):
        held += 1
        if held > 64:  # pragma: no cover — a runaway would hang the suite
            raise AssertionError("the semaphore never exhausted; it is unbounded")
    assert held == ud._ENRICHMENT_WORKERS, (
        f"expected {ud._ENRICHMENT_WORKERS} permits, drained {held}; capacity and "
        "worker count have drifted apart"
    )
    yield
    for _ in range(held):
        ud._enrichment_semaphore.release()


def test_a_saturated_pool_answers_without_submitting(
    client: TestClient, executor_spy: MagicMock, saturated: object,
) -> None:
    """With no permit free the write still succeeds, and nothing is submitted.

    This is the assertion an unused semaphore cannot satisfy: the permit has to
    be consulted at the submission point for the spy to stay untouched.
    """
    response = client.post("/remember", json={"content": "A note during a burst."})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("ok") is True, f"the durable write must still succeed: {body}"
    assert body.get("searchable_by") == "wording", (
        f"a saturated pool must degrade to wording-only, got {body.get('searchable_by')!r}"
    )
    executor_spy.assert_not_called()


def test_with_a_permit_free_the_pool_is_used(
    client: TestClient, executor_spy: MagicMock,
) -> None:
    """The other half, without which the test above proves nothing.

    An implementation that simply never submits would pass the saturation test
    while making inline enrichment dead code.
    """
    response = client.post("/remember", json={"content": "A note with room to spare."})

    assert response.status_code == 200, response.text
    executor_spy.assert_called()


def test_a_saturated_pool_leaves_the_permits_as_it_found_them(
    client: TestClient, executor_spy: MagicMock, saturated: object,
) -> None:
    """Declining must not release a permit it never held.

    A path that releases on the way out regardless would hand back a permit it
    does not hold, raising the effective capacity every time the pool saturates —
    worse than no bound at all, because it grows under load.
    """
    before = ud._enrichment_semaphore._value
    client.post("/remember", json={"content": "Another note during the burst."})
    assert ud._enrichment_semaphore._value == before, (
        "declining to submit changed the permit count"
    )


def test_the_permit_is_returned_by_the_worker() -> None:
    """Released when the work finishes, including when it raises.

    Releasing on the caller's timeout instead would hand the permit back while
    the worker still holds a pool thread, which is the unbounded queueing this is
    meant to prevent.
    """
    before = ud._enrichment_semaphore._value

    engine = MagicMock()
    engine.enrich_new_facts_now.return_value = 3
    assert ud._enrichment_semaphore.acquire(blocking=False)
    assert ud._enrich_and_release(engine, ["f1"], 0.5) == 3
    assert ud._enrichment_semaphore._value == before, "permit not returned on success"

    engine.enrich_new_facts_now.side_effect = RuntimeError("model not loaded")
    assert ud._enrichment_semaphore.acquire(blocking=False)
    with pytest.raises(RuntimeError):
        ud._enrich_and_release(engine, ["f1"], 0.5)
    assert ud._enrichment_semaphore._value == before, "permit not returned on failure"


def test_the_pool_is_never_replaced_by_the_default_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After shutdown the accessor must raise, never return None.

    ``run_in_executor(None, ...)`` means the event loop's own default executor —
    unbounded, and shared with every other handler. Returning None here would
    silently restore the behaviour this pool exists to replace, at the exact
    moment the process is trying to stop.
    """
    monkeypatch.setattr(ud, "_enrichment_pool", None)
    monkeypatch.setattr(ud, "_enrichment_pool_closed", True)
    with pytest.raises(RuntimeError):
        ud._enrichment_executor()
