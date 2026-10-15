"""PR-253 #4: real transition/middleware admission is inside the HTTP clock.

A hook's recall (``answer_check=skip``) keeps the three-second clock.
"""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

from superlocalmemory.server import recall_core
from superlocalmemory.server.profile_runtime import ProfileRuntime, ProfileRuntimeMiddleware


def test_exact_review_transition_cannot_keep_http_recall_waiting_3300ms(monkeypatch):
    runtime = ProfileRuntime("default")
    engine = object()
    state = SimpleNamespace(profile_runtime=runtime, engine=engine)
    entered, release = threading.Event(), threading.Event()

    def commit(snapshot):
        entered.set()
        release.wait(5)

    transition = threading.Thread(target=lambda: runtime.reconfigure(commit))
    transition.start()
    assert entered.wait(1)
    timer = threading.Timer(3.3, release.set)
    timer.start()
    monkeypatch.setattr(recall_core, "_engine_call", lambda *a: object())
    monkeypatch.setattr(recall_core, "_envelope", lambda *a: {"results": ["answer"]})

    async def run():
        messages = []

        async def route(scope, receive, send):
            payload = await recall_core.run_recall(
                engine, recall_core.RecallCall("a question?", 3, "", "test", True, skip_answer_check=True),
                app_state=state,
            )
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        start = time.monotonic()
        await ProfileRuntimeMiddleware(route, app_state=state)(
            {"type": "http", "path": "/recall", "method": "GET",
             "query_string": b"q=a+question%3F&answer_check=skip"}, receive, send,
        )
        assert time.monotonic() - start < 3.2
        assert json.loads(messages[-1]["body"])["degraded_reason"] == "recall_budget_exceeded"

    try:
        asyncio.run(run())
    finally:
        release.set()
        transition.join(2)
        timer.cancel()
        timer.join(1)
    assert runtime._active_operations == 0
