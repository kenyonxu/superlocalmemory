"""An explicit question's answer check gets the question's run clock.

4.2.3 on the author's store (27k facts): search took 3-5 s, the answer check
was held to the 3 s budget, and it was skipped as "budget_exhausted" on
almost every recall, though it costs 30-140 ms and the question was allowed
seconds more. The answer check is a headline feature; it must run whenever the
question itself finishes.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.server import profile_runtime, recall_core, recall_ingress

SNAP = SimpleNamespace(profile_id="default", generation=1)


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    monkeypatch.setattr(profile_runtime, "get_profile_runtime",
                        lambda state: SimpleNamespace(snapshot=SNAP))


def _call(**kw):
    base = dict(query="what am I working on", limit=3, session_id="", agent_id="a",
                fast=True, profile_id="default")
    base.update(kw)
    return recall_core.RecallCall(**base)


def _answer_window(monkeypatch, call, budget):
    seen = {}

    def engine(_engine, c):
        seen["left"] = c.answer_deadline - time.monotonic()
        return object()

    monkeypatch.setattr(recall_core, "_engine_call", engine)
    monkeypatch.setattr(recall_core, "_envelope", lambda *a: {"results": []})
    monkeypatch.setattr(recall_core, "effective_budget_s", lambda c: budget)

    async def run():
        token = recall_ingress._DEADLINE.set(
            asyncio.get_running_loop().time() + recall_core.EXPLICIT_CEILING_S + 0.5)
        try:
            await recall_core.run_recall(object(), call, app_state=SimpleNamespace())
        finally:
            recall_ingress._DEADLINE.reset(token)

    asyncio.run(run())
    return seen["left"]


def test_a_question_s_answer_check_outlasts_the_three_second_budget(monkeypatch):
    left = _answer_window(monkeypatch, _call(), 3.0)
    assert left > 3.0 + recall_core.EXPLICIT_RUN_GRACE_S - 1.0


def test_a_hook_keeps_its_short_answer_window(monkeypatch):
    left = _answer_window(monkeypatch, _call(fast=False, skip_answer_check=True), 3.0)
    assert left <= 3.0
