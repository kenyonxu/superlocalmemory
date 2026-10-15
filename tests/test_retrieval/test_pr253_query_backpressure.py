"""PR-253 #5: fifty timed-out queries cannot leave fifty pending embeddings."""
import threading
from types import SimpleNamespace

from superlocalmemory.retrieval.query_embedding import QueryEmbedder


def test_exact_review_fifty_distinct_timeouts_have_bounded_queue_and_inflight():
    release = threading.Event()
    calls = []

    def embed(query):
        calls.append(query)
        release.wait(3)
        return [1.0]

    provider = SimpleNamespace(is_warm=True, embed=embed)
    qe = QueryEmbedder(lambda: provider)
    try:
        for index in range(50):
            assert qe.embed(f"distinct query {index}", 0.001)[0] is None
        assert len(qe._inflight) <= 2
        assert qe._executor._work_queue.qsize() <= 2
        assert len(calls) <= 2
    finally:
        threads = qe.worker_threads()
        qe.close()
        release.set()
        for thread in threads:
            thread.join(1)
    assert not any(thread.is_alive() for thread in threads)


def test_generation_switch_does_not_admit_more_work_behind_blocked_old_provider():
    release = threading.Event()
    old = SimpleNamespace(is_warm=True, embed=lambda q: (release.wait(3), [1.0])[1])
    new_calls = []
    new = SimpleNamespace(embed=lambda q: (new_calls.append(q), [2.0])[1])
    holder = {"provider": old}
    qe = QueryEmbedder(lambda: holder["provider"])
    try:
        qe.embed("old one", 0.005)
        qe.embed("old two", 0.005)
        holder["provider"] = new
        for index in range(50):
            qe.embed(f"new question {index}", 0.001)
        assert qe._executor._work_queue.qsize() <= 2
        assert not new_calls
    finally:
        threads = qe.worker_threads()
        qe.close()
        release.set()
        for thread in threads:
            thread.join(1)
    assert "old one" not in qe.cache
    assert not new_calls


def test_queued_work_losing_its_last_caller_never_runs_inference():
    from concurrent.futures import ThreadPoolExecutor
    release = threading.Event()
    entered = threading.Event()
    calls = []

    def embed(question):
        calls.append(question)
        entered.set()
        release.wait(3)
        return [1.0]

    qe = QueryEmbedder(lambda: provider)
    provider = SimpleNamespace(is_warm=True, embed=embed)
    qe._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slm-query-embed")
    try:
        qe.embed("running", 0.01)
        assert entered.wait(1)
        qe.embed("abandoned queued", 0.001)
        for index in range(50):
            qe.embed(f"overloaded {index}", 0.001)
        assert qe._executor._work_queue.qsize() == 1
        release.set()
    finally:
        threads = qe.worker_threads()
        qe.close()
        release.set()
        for thread in threads:
            thread.join(1)
    assert calls == ["running"]
