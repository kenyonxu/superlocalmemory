"""ENG-17: a saved migration resumes through a cold managed worker's real lock."""

import re
from types import SimpleNamespace

import pytest

from superlocalmemory.core import embedding_reindex as er
from superlocalmemory.core import embedding_reindex_steps as steps
from superlocalmemory.core import ram_lock
from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.runtimes.managed_text import ManagedTextEmbedder
from superlocalmemory.runtimes.worker_client import MediaWorkerClient
from superlocalmemory.storage import embedding_spaces as sp
from tests.helpers.env_capabilities import NO_VECTOR_SEARCH_REASON, vector_search_available
from tests.test_runtimes.conftest import StubEnv
from tests.test_storage.test_embedding_reindex_units import store  # noqa: F401


@pytest.fixture(autouse=True)
def _target_model_is_approved_for_text(monkeypatch):
    """These tests are about the re-index mechanics on a managed model. Whether a
    managed model may take text at all is tests/test_storage/test_every_switch_door_is_guarded.py."""
    from superlocalmemory.retrieval import support_recipe
    monkeypatch.setattr(support_recipe, "approved_for_text", lambda model: True)


MEMORY_MESSAGE = "Not enough free memory to load the model right now; try again later."

pytestmark = pytest.mark.skipif(not vector_search_available(), reason=NO_VECTOR_SEARCH_REASON)


def test_cold_managed_migration_resumes_saved_cursor(store, monkeypatch):  # noqa: F811
    root, db_path = store
    monkeypatch.setattr(ram_lock, "RAM_LOCK_PATH", root / "ram.sem")
    monkeypatch.setattr(ram_lock.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=8 * 1024**3))
    monkeypatch.setattr(steps, "DUTY_REST", 0)
    client = MediaWorkerClient(StubEnv(root), model_id="fake:4", revision="",
                               load_timeout_s=2, rss_limit_mb=0)
    target = EmbeddingConfig(provider="slm-media", model_name="fake:4", dimension=4)
    first = ManagedTextEmbedder(target, client_supplier=lambda: client)
    runner = er.ReindexRunner(db_path=db_path, data_root=root)
    queued = runner.request_switch(target, prebuilt=first)
    conn = sp.connect(db_path)
    try:
        job = er.get_job(conn, queued["job_id"])
        job = runner._prepare(job, first, 4)
        facts = [(r[0], r[1], r[2], r[3]) for r in conn.execute(
            "SELECT rowid, fact_id, profile_id, content FROM atomic_facts ORDER BY rowid LIMIT 4")]
        steps.stage(conn, db_path, job, first, facts, 4, rollback=False,
                    stats=steps.LockStats(), cursor=facts[-1][0], catching_up=False)
        job = er.get_job(conn, job["job_id"])
        assert job["done"] == 4
        first.shutdown()
        assert client.pid is None
        monkeypatch.setattr(steps, "build_embedder", lambda cfg: ManagedTextEmbedder(
            cfg, client_supplier=lambda: client))
        resumed = er.ReindexRunner(db_path=db_path, data_root=root)
        resumed._run(job)
        final = er.get_job(conn, job["job_id"])
        assert final["state"] == "activated", final
        assert final["done"] == 20
        assert '"resumed_at_done": 4' in final["stats"]
        assert sp.vec_dimension(conn, "fact_embeddings") == 4
        assert sp.vec_dimension(conn, sp.PREV_VEC) == 8
    finally:
        conn.close()
        first.shutdown()
        client.stop()


def test_nonmanaged_model_still_reserves_memory(store, monkeypatch):  # noqa: F811
    root, db_path = store
    monkeypatch.setattr(ram_lock.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=100 * 1024**2))
    runner = er.ReindexRunner(db_path=db_path, data_root=root)
    target = EmbeddingConfig(provider="sentence-transformers", model_name="new", dimension=4)
    with pytest.raises(steps.StepFailed, match=re.escape(MEMORY_MESSAGE)):
        runner._load_embedder(target)


def test_managed_worker_still_refuses_loading_below_memory_floor(store, monkeypatch):  # noqa: F811
    root, db_path = store
    monkeypatch.setattr(ram_lock.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=100 * 1024**2))
    client = MediaWorkerClient(StubEnv(root), model_id="fake:4", revision="")
    monkeypatch.setattr(steps, "build_embedder", lambda cfg: ManagedTextEmbedder(
        cfg, client_supplier=lambda: client))
    target = EmbeddingConfig(provider="slm-media", model_name="fake:4", dimension=4)
    try:
        runner = er.ReindexRunner(db_path=db_path, data_root=root)
        with pytest.raises(steps.StepFailed, match=re.escape(MEMORY_MESSAGE)):
            runner._load_embedder(target)
        assert client.pid is None
    finally:
        client.stop()
