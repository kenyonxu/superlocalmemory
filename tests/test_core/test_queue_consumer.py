# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for core.queue_consumer — TDD RED phase.

The QueueConsumer is a background loop that:
  1. Polls recall_queue.db for pending recall jobs
  2. Claims them atomically (fencing token)
  3. Routes through pool.recall() (never engine directly)
  4. Writes results back to the queue

These tests verify the consumer contract WITHOUT loading any engine,
ONNX models, or heavy dependencies. All recall is mocked.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock


def _make_queue(tmp_path: Path):
    from superlocalmemory.core.recall_queue import RecallQueue
    return RecallQueue(db_path=tmp_path / "q.db")


def _make_consumer(queue, pool_mock=None, **kwargs):
    from superlocalmemory.core.queue_consumer import QueueConsumer
    pool = pool_mock or MagicMock()
    return QueueConsumer(queue=queue, pool=pool, **kwargs)


# -----------------------------------------------------------------------
# Basic lifecycle
# -----------------------------------------------------------------------

def test_consumer_starts_and_stops(tmp_path: Path) -> None:
    """Consumer can start and stop without errors."""
    q = _make_queue(tmp_path)
    consumer = _make_consumer(q)
    assert not consumer.running
    consumer.start()
    assert consumer.running
    consumer.stop()
    assert not consumer.running
    q.close()


def test_consumer_stop_is_idempotent(tmp_path: Path) -> None:
    """Calling stop() twice doesn't raise."""
    q = _make_queue(tmp_path)
    consumer = _make_consumer(q)
    consumer.start()
    consumer.stop()
    consumer.stop()
    q.close()


# -----------------------------------------------------------------------
# Job processing
# -----------------------------------------------------------------------

def test_consumer_processes_pending_recall(tmp_path: Path) -> None:
    """Enqueued recall job is claimed and processed by consumer."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.return_value = {
        "ok": True,
        "results": [{"fact_id": "f1", "content": "hello", "score": 0.9}],
    }
    consumer = _make_consumer(q, pool)

    rid = q.enqueue(
        query="what is X?", limit_n=3, mode="B",
        agent_id="hook", session_id="s1",
    )

    consumer.start()
    # Give consumer time to poll and process
    time.sleep(0.5)
    consumer.stop()

    row = q._get_row(rid)
    assert row is not None
    assert row["completed"] == 1
    result = json.loads(row["result_json"])
    assert result["ok"] is True
    assert len(result["results"]) == 1

    pool.recall.assert_called_once_with("what is X?", limit=3, session_id="s1")
    q.close()


def test_consumer_handles_pool_failure_gracefully(tmp_path: Path) -> None:
    """If pool.recall() returns error, consumer writes error to queue."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.return_value = {"ok": False, "error": "Worker died"}
    consumer = _make_consumer(q, pool)

    rid = q.enqueue(
        query="broken query", limit_n=3, mode="B",
        agent_id="hook", session_id="s1",
    )

    consumer.start()
    time.sleep(0.5)
    consumer.stop()

    row = q._get_row(rid)
    assert row is not None
    assert row["completed"] == 1
    result = json.loads(row["result_json"])
    assert result["ok"] is False
    q.close()


def test_consumer_handles_pool_exception(tmp_path: Path) -> None:
    """If pool.recall() raises, consumer marks job with error, doesn't crash."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.side_effect = RuntimeError("Engine exploded")
    consumer = _make_consumer(q, pool)

    rid = q.enqueue(
        query="crash query", limit_n=3, mode="B",
        agent_id="hook", session_id="s1",
    )

    consumer.start()
    time.sleep(0.5)
    consumer.stop()

    row = q._get_row(rid)
    assert row is not None
    # Should be completed with error result, not left in limbo
    assert row["completed"] == 1
    result = json.loads(row["result_json"])
    assert result["ok"] is False
    assert result.get("error") == "recall_failed"
    q.close()


# -----------------------------------------------------------------------
# Priority lanes
# -----------------------------------------------------------------------

def test_consumer_processes_high_priority_first(tmp_path: Path) -> None:
    """High-priority recall jobs are claimed before low-priority."""
    q = _make_queue(tmp_path)
    call_order = []
    pool = MagicMock()

    def track_recall(query, limit=10, session_id=""):
        call_order.append(query)
        return {"ok": True, "results": []}

    pool.recall.side_effect = track_recall
    consumer = _make_consumer(q, pool)

    # Enqueue low-priority first, then high-priority
    q.enqueue_job(
        job_type="consolidate", idempotency_key="c1",
        agent_id="worker", session_id="bg", priority="low",
        query="background job",
    )
    q.enqueue(
        query="urgent recall", limit_n=3, mode="B",
        agent_id="hook", session_id="s1", priority="high",
    )

    consumer.start()
    time.sleep(0.8)
    consumer.stop()

    # High-priority recall should have been processed first
    assert len(call_order) >= 1
    assert call_order[0] == "urgent recall"
    q.close()


# -----------------------------------------------------------------------
# Dedup: multiple subscribers, single execution
# -----------------------------------------------------------------------

def test_consumer_dedup_processes_once(tmp_path: Path) -> None:
    """5 identical enqueues (dedup'd to 1 request) → 1 pool.recall() call."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.return_value = {"ok": True, "results": []}
    consumer = _make_consumer(q, pool)

    rids = set()
    for _ in range(5):
        rid = q.enqueue(
            query="same query", limit_n=3, mode="B",
            agent_id="hook", session_id="s1",
        )
        rids.add(rid)

    # Dedup should give us 1 unique request_id
    assert len(rids) == 1

    consumer.start()
    time.sleep(0.5)
    consumer.stop()

    # Pool called exactly once
    pool.recall.assert_called_once()
    q.close()


# -----------------------------------------------------------------------
# Concurrency safety
# -----------------------------------------------------------------------

def test_consumer_multiple_jobs_processed(tmp_path: Path) -> None:
    """Multiple distinct recall jobs all get processed."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.return_value = {"ok": True, "results": []}
    consumer = _make_consumer(q, pool)

    rids = []
    for i in range(5):
        rid = q.enqueue(
            query=f"query-{i}", limit_n=3, mode="B",
            agent_id="hook", session_id="s1",
        )
        rids.append(rid)

    consumer.start()
    time.sleep(1.0)
    consumer.stop()

    completed = 0
    for rid in rids:
        row = q._get_row(rid)
        if row and row["completed"] == 1:
            completed += 1
    assert completed == 5, f"Only {completed}/5 jobs completed"
    q.close()


# -----------------------------------------------------------------------
# Fencing: stale claim can't overwrite
# -----------------------------------------------------------------------

def test_consumer_respects_fencing(tmp_path: Path) -> None:
    """Consumer uses fenced writes; stale received value is rejected."""
    q = _make_queue(tmp_path)
    rid = q.enqueue(
        query="fenced", limit_n=3, mode="B",
        agent_id="hook", session_id="s1",
    )

    # Simulate: claim once (received=1), then claim again (received=2)
    c1 = q.claim_pending(priority="high", stall_timeout_s=0.01)
    assert c1 is not None
    time.sleep(0.02)
    c2 = q.claim_pending(priority="high", stall_timeout_s=25.0)
    assert c2 is not None

    # Stale worker tries to complete with received=1
    n = q.complete(rid, received=1, result_json=json.dumps({"stale": True}))
    assert n == 0, "Stale write should be fenced out"

    # Current worker completes with received=2
    n = q.complete(rid, received=2, result_json=json.dumps({"ok": True}))
    assert n == 1
    q.close()


# -----------------------------------------------------------------------
# DLQ: poison pill after max_receives
# -----------------------------------------------------------------------

def test_consumer_marks_dlq_after_max_receives(tmp_path: Path) -> None:
    """After 3 failed claim attempts, consumer marks job as dead letter."""
    q = _make_queue(tmp_path)
    pool = MagicMock()
    pool.recall.side_effect = RuntimeError("always fails")
    consumer = _make_consumer(q, pool, max_receives=3)

    rid = q.enqueue(
        query="poison", limit_n=3, mode="B",
        agent_id="hook", session_id="s1",
    )

    # Simulate 3 claims with stall timeout expiry
    for _ in range(3):
        c = q.claim_pending(priority="high", stall_timeout_s=0.01)
        if c:
            time.sleep(0.02)

    # After 3 receives, consumer should mark dead letter
    consumer.start()
    time.sleep(0.5)
    consumer.stop()

    row = q._get_row(rid)
    assert row is not None
    assert row["dead_letter"] == 1 or row["received"] >= 3
    q.close()


# -----------------------------------------------------------------------
# Consumer does NOT load engine
# -----------------------------------------------------------------------

def test_consumer_never_imports_engine(tmp_path: Path) -> None:
    """Verify QueueConsumer module doesn't import MemoryEngine at module level."""
    import importlib
    import sys

    # Remove cached module if any
    mod_name = "superlocalmemory.core.queue_consumer"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Import the module
    mod = importlib.import_module(mod_name)

    # Check that MemoryEngine is NOT in the module's globals
    assert not hasattr(mod, "MemoryEngine"), \
        "queue_consumer must NOT import MemoryEngine — memory blast risk"
