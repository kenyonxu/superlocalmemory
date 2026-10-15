"""HTTP boundary contracts for the daemon-owned canonical remember path."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from starlette.requests import Request


def _remember_endpoint(app):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/remember"
    )


def _request(app) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/remember",
        "headers": [],
        "app": app,
    })


@pytest.fixture
def ordered_runtime(engine_with_mock_deps, monkeypatch):
    """Exercise real journal/coordinator admission after the route's trust hook."""
    from superlocalmemory.core.remember_runtime import CanonicalRememberRuntime

    engine = engine_with_mock_deps
    sequence: list[str] = []
    runtime = CanonicalRememberRuntime.for_engine(engine)
    original_remember = runtime.remember

    def trust_hook(operation, payload):
        assert operation == "store"
        assert payload["agent_id"] == "trusted-actor"
        sequence.append("hook")

    def remember(admission, actor, *, deadline_ms, accept_after_ms=None):
        assert sequence == ["hook"]
        assert admission.content == "canonical route witness"
        assert actor.principal_id == "trusted-actor"
        assert deadline_ms == 2_000  # the journal prepare keeps its budget
        assert accept_after_ms == 1_200  # inside the 1.5 s remember ceiling
        sequence.append("journal-coordinator")
        return original_remember(admission, actor, deadline_ms=deadline_ms,
                                 accept_after_ms=accept_after_ms)

    monkeypatch.setattr(engine._hooks, "run_pre", trust_hook)
    monkeypatch.setattr(runtime, "remember", remember)
    runtime.start()
    try:
        yield engine, runtime, sequence
    finally:
        runtime.stop()


async def _call_with_completion_barrier(app, wait, started, release):
    """A synchronous response cannot escape while materialization is blocked."""
    from superlocalmemory.server import unified_daemon

    task = asyncio.create_task(_remember_endpoint(app)(
        unified_daemon.RememberRequest(content="canonical route witness"),
        _request(app), wait=wait,
    ))
    try:
        if wait:
            assert await asyncio.to_thread(started.wait, 5)
            assert not task.done()
            release.set()
        return await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("wait", [False, True])
def test_remember_runs_trust_hook_before_journal_runtime_and_honours_wait(
    ordered_runtime, monkeypatch, wait,
) -> None:
    """Only wait=true crosses the real canonical completion barrier."""
    from superlocalmemory.core.ingestion_command import IngestionOperationRepository
    from superlocalmemory.server import remember_completion, unified_daemon

    engine, runtime, sequence = ordered_runtime
    repository = IngestionOperationRepository(engine._db)
    started, release = threading.Event(), threading.Event()
    original_complete = remember_completion.complete_remember

    def complete(bound_engine, payload, profile_id):
        assert wait, "wait=false must return without requesting completion"
        assert bound_engine is engine
        assert sequence == ["hook", "journal-coordinator"]
        assert repository.get(payload["operation_id"]).state.value == "queryable"
        started.set()
        assert release.wait(5)
        result = original_complete(bound_engine, payload, profile_id)
        sequence.append("complete")
        return result

    monkeypatch.setattr(remember_completion, "complete_remember", complete)
    monkeypatch.setattr(
        "superlocalmemory.server.write_identity.require_write_actor",
        lambda *_args, **_kwargs: "trusted-actor",
    )
    app = unified_daemon.create_app()
    app.state.engine = engine
    app.state.canonical_remember_runtime = runtime
    result = asyncio.run(_call_with_completion_barrier(app, wait, started, release))

    state = "complete" if wait else "queryable"
    assert sequence == ["hook", "journal-coordinator"] + (["complete"] if wait else [])
    assert started.is_set() is wait
    assert result["status"] == result["materialization_state"] == state
    assert result["wait_ignored"] is (not wait)
    operation = repository.get(result["operation_id"])
    assert operation.state.value == state
    assert result["fact_ids"] == list(operation.fact_ids)
    assert result["commit_sequence"] > 0


def test_remember_returns_retryable_503_when_canonical_writer_is_unavailable(
    tmp_path, monkeypatch,
) -> None:
    """A missing writer never falls back to a direct engine write."""
    from fastapi import HTTPException

    from superlocalmemory.server import unified_daemon

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "superlocalmemory.server.write_identity.require_write_actor",
        lambda *_args, **_kwargs: "trusted-actor",
    )
    app = unified_daemon.create_app()
    app.state.engine = SimpleNamespace(
        _profile_id="default",
        _config=SimpleNamespace(scope=SimpleNamespace(default_scope="personal")),
        _hooks=SimpleNamespace(run_pre=lambda *_args, **_kwargs: None),
    )
    app.state.canonical_remember_runtime = None

    try:
        asyncio.run(_remember_endpoint(app)(
            unified_daemon.RememberRequest(content="unavailable witness"),
            _request(app),
        ))
    except HTTPException as exc:
        assert exc.status_code == 503
    else:  # pragma: no cover - explicit fail-closed contract
        raise AssertionError("canonical remember unexpectedly fell back to an engine write")
