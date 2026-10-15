"""The embedding status API and CLI explain a pressure pause without starting workers."""

from types import SimpleNamespace

import pytest

from superlocalmemory.cli.embedder_cmd import _status_text
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embedding_memory import guard_for
from superlocalmemory.core.embedding_reindex import ReindexRunner
from superlocalmemory.core.embeddings import EmbeddingService
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available
from tests.test_storage.test_embedding_reindex_units import store  # noqa: F401

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)


def test_status_surfaces_memory_pause_and_recovery_threshold(store):  # noqa: F811
    root, db_path = store
    service = EmbeddingService(EmbeddingConfig())
    try:
        guard_for(service).block()
        state = SimpleNamespace(engine=SimpleNamespace(_embedder=service))
        data = ReindexRunner(db_path=db_path, data_root=root, app_state=state).status()
        assert data["worker"]["state"] == "memory_pressure"
        assert data["worker"]["recovery_mb"] >= 2560
        assert data["worker"]["retry_in_s"] > 0
        assert data["worker"]["reason"] in _status_text(data)
        assert service._worker_proc is None
    finally:
        service.shutdown()
