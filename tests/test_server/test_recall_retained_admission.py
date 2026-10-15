"""An admitted request can fork work while a transition is draining it."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from superlocalmemory.server import recall_core
from superlocalmemory.server.profile_runtime import ProfileRuntime, ProfileRuntimeMiddleware


def test_recall_does_not_wait_behind_its_own_request_lease(monkeypatch):
    runtime = ProfileRuntime("default")
    engine = object()
    state = SimpleNamespace(profile_runtime=runtime, engine=engine)
    worker = threading.Thread(target=runtime.transition, args=("next", lambda *a: None))
    observed = []
    monkeypatch.setattr(recall_core, "effective_budget_s", lambda call: 0.15)
    monkeypatch.setattr(recall_core, "_engine_call", lambda *a: object())
    monkeypatch.setattr(recall_core, "_envelope", lambda *a: {"worked": True})
    monkeypatch.setattr(recall_core, "_fallback", lambda *a: {})

    async def app(scope, receive, send):
        worker.start()
        while not runtime.transitioning:
            await asyncio.sleep(0.001)
        observed.append(await recall_core.run_recall(
            engine, recall_core.RecallCall("a question?", 3, "", "test", True),
            app_state=state))
        await send({"type": "http.response.start", "status": 200})

    async def noop(*args):
        return None

    try:
        asyncio.run(ProfileRuntimeMiddleware(app, app_state=state)(
            {"type": "http", "path": "/recall", "method": "GET"}, noop, noop))
        worker.join(1)
        assert observed == [{"worked": True}]
        assert not worker.is_alive()
        assert runtime.snapshot.profile_id == "next"
        assert runtime.active_operations == 0
    finally:
        worker.join(1)


def test_fork_survives_parent_release_but_closed_parent_cannot_fork():
    runtime = ProfileRuntime("default")
    parent = runtime.acquire_lease()
    child = parent.fork()
    parent.release()
    parent.release()
    assert runtime.active_operations == 1
    with pytest.raises(RuntimeError, match="expired"):
        parent.fork()
    child.release()
    assert runtime.active_operations == 0


def test_queued_work_releases_its_lease_when_recall_owner_has_left():
    from superlocalmemory.core.recall_gate import RecallHold
    from superlocalmemory.server.recall_admission import foreground_admission
    from superlocalmemory.server.recall_deadline import submit

    async def run():
        runtime = ProfileRuntime("default")
        queued = []
        loop = SimpleNamespace(run_in_executor=lambda pool, fn: queued.append(fn))
        hold = RecallHold()
        try:
            async with foreground_admission(runtime, asyncio.get_running_loop().time() + 1):
                submit(loop, hold, runtime, SimpleNamespace(), object(),
                       lambda: pytest.fail("abandoned recall executed"))
                assert runtime.active_operations == 2
                hold.leave()
            assert runtime.active_operations == 0
            runtime.transition("next", lambda *a: None)
            assert runtime.snapshot.profile_id == "next"
            queued.pop()()
            assert runtime.active_operations == 0
        finally:
            hold.leave()
    asyncio.run(run())


def test_repeated_middleware_cancellation_releases_late_admission(monkeypatch):
    runtime = ProfileRuntime("default")
    entered, release, completed = (threading.Event() for _ in range(3))
    original = runtime.acquire_lease

    def delayed():
        entered.set()
        release.wait(1)
        lease = original()
        completed.set()
        return lease

    monkeypatch.setattr(runtime, "acquire_lease", delayed)

    async def noop(*args):
        return None

    async def run():
        middleware = ProfileRuntimeMiddleware(
            noop, app_state=SimpleNamespace(profile_runtime=runtime))
        task = asyncio.create_task(middleware(
            {"type": "http", "path": "/recall", "method": "GET"}, noop, noop))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        while not completed.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        assert runtime.active_operations == 0
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_expired_fresh_admission_releases_the_eventual_lease():
    from superlocalmemory.server.recall_admission import foreground_admission

    async def run():
        runtime = ProfileRuntime("default")
        runtime._transitioning = True
        try:
            deadline = asyncio.get_running_loop().time() + 0.02
            async with foreground_admission(runtime, deadline) as ok:
                assert not ok
        finally:
            with runtime._condition:
                runtime._transitioning = False
                runtime._condition.notify_all()
        # Let the pending acquisition and its release callback settle.
        for _ in range(100):
            await asyncio.sleep(0.001)
        assert runtime.active_operations == 0
    asyncio.run(run())


def test_running_dispatch_keeps_lease_after_owner_leaves():
    from superlocalmemory.core.recall_gate import RecallHold
    from superlocalmemory.server.recall_admission import foreground_admission
    from superlocalmemory.server.recall_deadline import submit

    async def run():
        runtime = ProfileRuntime("default")
        entered, release = threading.Event(), threading.Event()
        hold = RecallHold()

        def work():
            entered.set()
            release.wait(1)

        try:
            async with foreground_admission(runtime, asyncio.get_running_loop().time() + 1):
                future = submit(asyncio.get_running_loop(), hold, runtime,
                                SimpleNamespace(), object(), work)
                while not entered.is_set():
                    await asyncio.sleep(0.001)
                hold.leave()
            assert runtime.active_operations == 1
            release.set()
            await future
            assert runtime.active_operations == 0
        finally:
            hold.leave()
            release.set()
    asyncio.run(run())
