"""Waiting for an embedding worker is visible wherever job status is shown."""

from superlocalmemory.storage.embedding_reindex_jobs import public_view


def test_queued_embedding_upgrade_explains_why_recall_has_not_changed():
    job = {
        "job_id": "q1",
        "kind": "text",
        "state": "queued",
        "from_signature": "old",
        "to_signature": "new",
        "created_at": 1.0,
        "total": 4,
    }
    view = public_view(job)
    assert "background worker" in view["queued_reason"]
    assert "current model" in view["queued_reason"]
    assert public_view({**job, "state": "running"})["queued_reason"] is None
