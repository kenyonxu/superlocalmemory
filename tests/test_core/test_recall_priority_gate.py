"""Foreground recall priority for the shared embedding worker."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embeddings import EmbeddingService
from superlocalmemory.core.materialization_control import MaterializationDeferred
from superlocalmemory.core.recall_gate import (
    background_work,
    begin_recall,
    end_recall,
)


def _service() -> EmbeddingService:
    service = EmbeddingService.__new__(EmbeddingService)
    service._config = SimpleNamespace(
        is_openai_compatible=False,
        is_cloud=False,
        dimension=1,
    )
    return service


def test_background_embed_waits_for_active_recall(monkeypatch) -> None:
    service = _service()
    entered = threading.Event()

    def fake_subprocess(texts):
        entered.set()
        return [[1.0] for _ in texts]

    monkeypatch.setattr(service, "_subprocess_embed", fake_subprocess)
    begin_recall()
    try:
        def run_background() -> None:
            with background_work():
                service.embed("background")

        worker = threading.Thread(target=run_background)
        worker.start()
        time.sleep(0.05)
        assert not entered.is_set()
    finally:
        end_recall()
    worker.join(timeout=1.0)
    assert entered.is_set()
    assert not worker.is_alive()


def test_background_batch_is_sliced_for_preemption(monkeypatch) -> None:
    service = _service()
    batches: list[list[str]] = []

    def fake_subprocess(texts):
        batches.append(list(texts))
        return [[1.0] for _ in texts]

    monkeypatch.setattr(service, "_subprocess_embed", fake_subprocess)
    with background_work():
        assert service.embed_batch(["one", "two", "three"]) == [
            [1.0], [1.0], [1.0],
        ]
    assert batches == [["one"], ["two"], ["three"]]


def test_background_remote_embedding_defers_after_runtime_preemption() -> None:
    """A profile reconfigure must not wait behind a 30-second HTTP read.

    Background materialization is allowed to defer its optional enrichment.
    It is not allowed to turn a reconfiguration into a drain timeout or to
    retry the request after the runtime has asked it to yield.
    """
    import httpx

    service = EmbeddingService(EmbeddingConfig(
        provider="openai",
        api_endpoint="http://embedder.invalid/v1",
        model_name="test-model",
        dimension=1,
    ))
    preempted = threading.Event()
    timeouts: list[object] = []

    class _SlowClient:
        def post(self, *args, **kwargs):
            timeouts.append(kwargs["timeout"])
            preempted.set()
            raise httpx.ReadTimeout("profile reconfigure requested")

    service._http_client = _SlowClient()
    with background_work(preempt_requested=preempted.is_set), pytest.raises(
        MaterializationDeferred,
    ):
        service._openai_compatible_embed_batch(["must yield"])

    assert len(timeouts) == 1
    assert float(timeouts[0]) < 5.0


def test_local_embedding_worker_is_warm_only_after_successful_request() -> None:
    service = EmbeddingService.__new__(EmbeddingService)
    service._worker_proc = None
    service._request_count = 0

    assert service.is_warm is False

    service._worker_proc = SimpleNamespace(poll=lambda: None)
    assert service.is_warm is False

    service._request_count = 1
    assert service.is_warm is True

    service._worker_proc = SimpleNamespace(poll=lambda: 1)
    assert service.is_warm is False


def test_openai_embedding_is_warm_after_successful_http_request(monkeypatch) -> None:
    service = EmbeddingService(EmbeddingConfig(
        provider="openai",
        api_endpoint="http://localhost:8045/v1",
        dimension=1,
    ))
    monkeypatch.setattr(
        service,
        "_openai_compatible_embed_batch",
        lambda texts: [[1.0] for _ in texts],
    )

    assert service.is_warm is False
    assert service.embed("ready") == [1.0]
    assert service.is_warm is True


def test_cloud_embedding_is_warm_after_successful_http_request(monkeypatch) -> None:
    service = EmbeddingService(EmbeddingConfig(
        provider="cloud",
        api_endpoint="https://example.invalid",
        api_key="test-key",
        dimension=1,
    ))
    monkeypatch.setattr(service, "_cloud_embed_single", lambda text: [1.0])

    assert service.is_warm is False
    assert service.embed("ready") == [1.0]
    assert service.is_warm is True
