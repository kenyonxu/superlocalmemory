# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later

"""Destructive worker writes derive authority from the local capability."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from superlocalmemory.core import recall_worker
from superlocalmemory.core.ingestion_command import IngestionState


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.profile_id = "default"
    engine._profile_id = "default"
    engine._embedder = None
    engine._retrieval_engine = None
    # No projection backends: delete proves erasure across stores, and a bare
    # MagicMock store would fabricate residue. None means "nothing to prove".
    engine._vector_store = None
    engine._ann_index = None
    engine._db.db_path = "/nonexistent/memory.db"

    def _execute(sql, params=()):
        text = " ".join(str(sql).split()).lower()
        # The fact-content read returns the row; every erasure presence/existence
        # probe returns empty so a delete verifies as "nothing remains".
        if text.startswith("select content") and "from atomic_facts" in text:
            return [{"content": "old content", "memory_id": None}]
        # The review-gated correction ledger (M042) must look PRESENT.
        # mutations.py::_correction_ledger_available probes sqlite_master for
        # the correction_cases table and update_memory fails CLOSED when it is
        # missing — correct product behaviour, and the reason this test began
        # failing once corrections became a review-gated lifecycle rather than
        # an in-place edit. Returning [] here modelled a database that had never
        # been migrated, which no real install is in. This test is about WHICH
        # ACTOR a destructive write is attributed to, not about ledger
        # availability, so the fixture must let it reach the identity
        # assertions. Ledger-absent behaviour is covered separately by the
        # compliance suite.
        if "sqlite_master" in text and "correction_cases" in text:
            return [{"1": 1}]
        return []

    engine._db.execute.side_effect = _execute
    return engine


def test_delete_uses_capability_actor_and_treats_agent_label_as_metadata(
    monkeypatch,
) -> None:
    engine = _engine()
    monkeypatch.setattr(recall_worker, "_get_engine", lambda: engine)
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )

    result = recall_worker._handle_delete_memory(
        "fact-1",
        source_agent_id="caller-selected-admin",
    )

    assert result["ok"] is True
    engine._hooks.run_pre.assert_called_once_with(
        "delete",
        {
            "operation": "delete",
            "agent_id": "trusted:recall-worker",
            "source_agent_id": "caller-selected-admin",
            "profile_id": "default",
            "fact_id": "fact-1",
        },
    )
    engine._db.delete_fact.assert_called_once_with("fact-1", profile_id="default")


def test_update_from_worker_refuses_non_retryably_with_a_remedy(
    monkeypatch,
) -> None:
    """Corrections are daemon-only, and the worker must say so honestly.

    Rewritten in 4.0.6. This test previously asserted ok is True, which stopped
    being achievable once corrections became a review-gated lifecycle (M042):
    update_fact_authorized needs the canonical correction writer, and that
    writer is the daemon's single-writer boundary. A worker subprocess cannot
    own it — CanonicalRememberRuntime.ready requires a live worker of its own,
    so building one here would nest workers and duplicate an ownership context
    that must stay exclusive.

    The old code called through regardless and surfaced
    "canonical correction writer is temporarily unavailable" with
    retryable=True, so a caller with the daemon down would retry an operation
    that can never succeed. What this test now pins is the honest contract:
    refuse, say it is not retryable, and name the remedy.
    """
    engine = _engine()
    monkeypatch.setattr(recall_worker, "_get_engine", lambda: engine)

    result = recall_worker._handle_update_memory(
        "fact-1",
        "new content",
        source_agent_id="caller-selected-admin",
    )

    assert result["ok"] is False
    # Non-retryable is the whole point: retrying without a daemon cannot work.
    assert result["retryable"] is False
    assert "slm serve start" in result["remedy"]
    assert "daemon" in result["error"].lower()
    # And it must NOT claim to be a transient condition.
    assert "temporarily unavailable" not in result["error"].lower()


def test_store_uses_capability_actor_before_canonical_persistence(
    monkeypatch,
) -> None:
    engine = _engine()
    canonical_store = MagicMock(return_value=SimpleNamespace(
        fact_ids=("fact-1",),
        operation_id="operation-1",
        state=IngestionState.COMPLETE,
    ))
    monkeypatch.setattr(recall_worker, "_get_engine", lambda: engine)
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.local_trusted_actor_id",
        lambda kind: f"trusted:{kind}",
    )
    monkeypatch.setattr(
        "superlocalmemory.core.engine_ingestion.canonical_store",
        canonical_store,
    )

    result = recall_worker._handle_store(
        "remember this",
        {
            "agent_id": "caller-selected-admin",
            "scope": "shared",
            "shared_with": ["research"],
            "idempotency_key": "request-1",
        },
    )

    assert result == {
        "ok": True,
        "fact_ids": ["fact-1"],
        "count": 1,
        "operation_id": "operation-1",
        "pending_id": None,
        "materialization_state": "complete",
    }
    canonical_store.assert_called_once_with(
        engine,
        "remember this",
        source_type="mcp-offline-worker",
        trusted_actor_id="trusted:recall-worker",
        metadata={"agent_id": "caller-selected-admin"},
        scope="shared",
        shared_with=["research"],
        session_id="",
        idempotency_key="request-1",
        return_receipt=True,
    )
