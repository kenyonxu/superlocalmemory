"""Exercise the production channel wait with a virtual completion clock."""

import concurrent.futures

import pytest

from superlocalmemory.retrieval import channel_status
from superlocalmemory.retrieval import engine as engine_mod
from superlocalmemory.retrieval.strategy import QueryStrategy
from tests.test_retrieval.test_a_dead_channel_does_not_look_like_an_empty_store import (
    _Channel,
    _engine,
)


@pytest.mark.parametrize(("duration", "complete"), [(1.983 * 3, True), (8.001, False)])
def test_guard_waits_for_loaded_channel_and_discloses_a_hang(monkeypatch, duration, complete):
    jobs = {}

    class VirtualExecutor:
        def submit(self, fn, *args):
            future = concurrent.futures.Future()
            jobs[future] = (fn, args)
            return future

    def wait(futures, *, timeout):
        assert timeout == engine_mod.CHANNEL_HANG_GUARD_SECONDS
        pending = set(futures)
        if duration <= timeout:
            for future in pending:
                fn, args = jobs[future]
                future.set_result(fn(*args))
            return pending, set()
        return set(), pending

    monkeypatch.setattr(concurrent.futures, "wait", wait)
    eng = _engine({"temporal": _Channel("ok", [("fact-a", 0.9)])}, disabled=("media",))
    eng._channel_executor.shutdown(wait=True)
    eng._channel_executor = VirtualExecutor()
    statuses, dropped = {}, set()
    try:
        result = eng._run_channels("query", "default", QueryStrategy(),
                                   channel_status=statuses, dropped_channels=dropped)
    finally:
        # Restore an executor with its normal shutdown contract for close().
        eng._channel_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        eng.close()
    if complete:
        assert result["temporal"] == [("fact-a", 0.9)] and not dropped
        assert statuses["temporal"] == channel_status.OK
    else:
        assert "temporal" not in result and dropped == {"temporal"}
        assert statuses["temporal"] == channel_status.TIMEOUT
