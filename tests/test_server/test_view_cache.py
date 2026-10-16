"""Dashboard summaries: one computation per key per few seconds, shared."""

import threading
import time

from superlocalmemory.server import view_cache


def test_repeats_share_one_build_and_get_copies():
    calls = []

    def build():
        calls.append(1)
        return {"n": [1]}

    a = view_cache.cached("t", ("p",), 5, build)
    b = view_cache.cached("t", ("p",), 5, build)
    assert calls == [1] and a == b
    a["n"].append(2)
    assert view_cache.cached("t", ("p",), 5, build) == {"n": [1]}
    assert view_cache.cached("t", ("other",), 5, build) == {"n": [1]} and len(calls) == 2


def test_expiry_and_drop():
    calls = []
    view_cache.cached("t", (), 0.05, lambda: calls.append(1))
    time.sleep(0.08)
    view_cache.cached("t", (), 0.05, lambda: calls.append(1))
    assert len(calls) == 2
    view_cache.drop("t")
    view_cache.cached("t", (), 5, lambda: calls.append(1))
    assert len(calls) == 3


def test_concurrent_callers_wait_for_the_one_build():
    calls, gate = [], threading.Event()

    def build():
        calls.append(1)
        gate.wait(1)
        return 7

    out = []
    threads = [threading.Thread(target=lambda: out.append(view_cache.cached("c", (), 5, build)))
               for _ in range(6)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(2)
    assert calls == [1] and out == [7] * 6


def test_a_failed_build_is_not_kept():
    def boom():
        raise RuntimeError("x")

    try:
        view_cache.cached("f", (), 5, boom)
    except RuntimeError:
        pass
    assert view_cache.cached("f", (), 5, lambda: "ok") == "ok"
