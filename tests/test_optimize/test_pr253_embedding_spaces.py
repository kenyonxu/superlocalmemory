"""PR-253 #8: isolated real cache and lazy factory cannot compare distinct spaces."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.core.embedding_recipe import LEGACY_TASK_SUFFIX
from superlocalmemory.optimize.cache.manager import _LazySemanticEmbedder
from superlocalmemory.optimize.cache.semantic import VCacheSemantic
from superlocalmemory.optimize.config.schema import OptimizeConfig
from superlocalmemory.optimize.storage.db import CacheDB
from superlocalmemory.storage.models import Mode


class Delegate:
    is_available = True

    def __init__(self, config):
        self._config = config
        self.dimension = config.dimension

    def embed(self, text):
        vector = [0.0] * self.dimension
        vector[0 if "France" in text or text.startswith("search_document: ") else 1] = 1.0
        return vector

    def unload(self):
        pass


def request(question):
    body = {"model": "fixture-local-model", "messages": [{"role": "user", "content": question}]}
    return SimpleNamespace(provider="fixture", method="POST", path="/chat", headers={},
                           body=body, body_bytes=json.dumps(body).encode(), stream=False,
                           has_tools=False)


def setup(root, monkeypatch):
    config = SLMConfig.for_mode(Mode.A, base_dir=root)
    config.embedding = replace(config.embedding, provider="sentence-transformers")
    monkeypatch.setattr(SLMConfig, "load", lambda *a, **kw: config)
    monkeypatch.setattr("superlocalmemory.core.embeddings.EmbeddingService", Delegate)
    policy = OptimizeConfig(enabled=True, cache_enabled=True, semantic_enabled=True,
                            semantic_centroid_defense=True, semantic_return_threshold=0.98,
                            semantic_pad_latency_ms=0.0)
    db = CacheDB(db_path=root / "cache.db")
    return config, policy, db


def train(cache, db, question, answer):
    cache.index_entry(request(question), "fixture", None, {"text": answer})
    entry = cache._derive_entry_id("fixture", question)
    for score in (0.97, 0.98, 0.99, 0.96, 0.95):
        cache.learn(entry, score, True)
    return entry


@pytest.mark.parametrize("restart", [True, False])
def test_exact_review_same_width_recipe_collision_never_returns_paris_for_germany(
    tmp_path, monkeypatch, restart,
):
    config, policy, db = setup(tmp_path, monkeypatch)
    cache = VCacheSemantic(db, policy, embedder=_LazySemanticEmbedder(),
                           embedding_dimension=768)
    try:
        train(cache, db, "What is the capital of France?", "Paris")
        if restart:
            cache.close()
            cache = VCacheSemantic(db, policy, embedder=_LazySemanticEmbedder(),
                                   embedding_dimension=768)
        config.embedding = replace(
            config.embedding, model_name=config.embedding.model_name + LEGACY_TASK_SUFFIX)
        assert cache.lookup(request("What is the capital of Germany?"), "fixture", None) is None
        assert "@retrieval-v1" in cache._embedder._service.model_name
    finally:
        cache.close()
        db.close()


def test_unknown_legacy_metadata_is_not_loaded_under_a_known_space(tmp_path, monkeypatch):
    import numpy as np
    config, policy, db = setup(tmp_path, monkeypatch)
    db.vec_add("legacy", "fixture", np.eye(1, 768, dtype=np.float32).tobytes(),
               {"model": "configured-embedding-provider", "dim": 768})
    db.set_with_entry_id(key="sem:legacy", tenant_id="fixture", value=b'{"text":"Paris"}',
                         entry_id="legacy", tags=["semantic"])
    cache = VCacheSemantic(db, policy, embedder=_LazySemanticEmbedder(),
                           embedding_dimension=768)
    try:
        assert cache.lookup(request("What is the capital of France?"), "fixture", None) is None
        assert cache._index["fixture"] == []
        assert cache._centroid_store.count("fixture") == 0
    finally:
        cache.close()
        db.close()


def test_restart_and_rollback_keep_response_boundaries_and_centroids_in_their_space(
    tmp_path, monkeypatch,
):
    config, policy, db = setup(tmp_path, monkeypatch)
    legacy = config.embedding
    cache = VCacheSemantic(db, policy, embedder=_LazySemanticEmbedder(),
                           embedding_dimension=768)
    question = "What is the capital of France?"
    try:
        old_id = train(cache, db, question, "Paris")
        config.embedding = replace(legacy, model_name=legacy.model_name + LEGACY_TASK_SUFFIX)
        new_id = train(cache, db, question, "new-space-response")
        assert new_id != old_id
        assert cache._centroid_store.count("fixture") == 1
        assert len(cache._boundary_store.get(new_id).samples) == 5
        rows = db._db.execute("SELECT model_name,vector_dim FROM llmcache_semantic_vectors")
        assert len({dict(r)["model_name"] for r in rows}) == 2
        assert all(json.loads(dict(r)["model_name"])["dimension"] == 768 for r in rows)
        cache.close()
        cache = VCacheSemantic(db, policy, embedder=_LazySemanticEmbedder(),
                           embedding_dimension=768)
        assert cache.lookup(request(question), "fixture", None) == {"text": "new-space-response"}
        config.embedding = legacy
        assert cache.lookup(request(question), "fixture", None) == {"text": "Paris"}
        assert cache._centroid_store.count("fixture") == 1
        assert len(cache._index["fixture"]) == 1
    finally:
        cache.close()
        db.close()


def test_activation_invalidation_releases_loaded_lazy_provider(tmp_path, monkeypatch):
    from superlocalmemory.optimize.cache.semantic_embedder import invalidate_embedding_space
    config, policy, db = setup(tmp_path, monkeypatch)
    embedder = _LazySemanticEmbedder()
    try:
        embedder("France")
        assert embedder._service is not None
        invalidate_embedding_space()
        assert embedder._service is None
        config.embedding = replace(
            config.embedding, model_name=config.embedding.model_name + LEGACY_TASK_SUFFIX)
        embedder("Germany")
        assert "@retrieval-v1" in embedder._service.model_name
    finally:
        embedder.close()
        db.close()
