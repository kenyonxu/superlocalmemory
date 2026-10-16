# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Lazy provider whose vector and persisted identity come from one snapshot."""
import hashlib
import json
import logging
import threading
import weakref

_OWNERS = weakref.WeakSet()
_OWNERS_LOCK = threading.Lock()


def space_identity(config):
    embedding = config.embedding
    endpoint = embedding.api_endpoint or (
        embedding.ollama_base_url if embedding.provider == "ollama" else "")
    return json.dumps({
        "provider": embedding.provider or "sentence-transformers",
        "model": embedding.model_name, "dimension": embedding.dimension,
        "endpoint": hashlib.sha256(endpoint.encode()).hexdigest(),
        "deployment": embedding.deployment_name,
        "cache_recipe": "uniform-document-v2",
    }, sort_keys=True, separators=(",", ":"))


class LazySemanticEmbedder:
    def __init__(self):
        self._lock = threading.RLock()
        self._service = None
        self._identity = None
        with _OWNERS_LOCK:
            _OWNERS.add(self)

    def __call__(self, text):
        return self.embed_with_identity(text)[0]

    def embed_with_identity(self, text):
        from superlocalmemory.core.config import SLMConfig
        from superlocalmemory.core.embeddings import EmbeddingService
        from superlocalmemory.core.engine_wiring import init_embedder

        with self._lock:
            config = SLMConfig.load()
            identity = space_identity(config)
            if identity != self._identity:
                self.close()
                name = config.embedding.model_name
                versioned = "@retrieval-" in name
                self._service = (init_embedder(config)
                                 if config.embedding.provider == "slm-media" or versioned
                                 else EmbeddingService(config.embedding))
                self._identity = identity
            vector = self._service.embed(text) if self._service is not None else None
            return vector, identity

    @property
    def dimension(self):
        from superlocalmemory.core.config import SLMConfig
        return int(SLMConfig.load().embedding.dimension)

    def close(self):
        with self._lock:
            service, self._service = self._service, None
            self._identity = None
            if service is not None:
                service.unload()


def invalidate_embedding_space():
    with _OWNERS_LOCK:
        owners = tuple(_OWNERS)
    for owner in owners:
        try:
            owner.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("Semantic provider invalidation failed: %s",
                                                type(exc).__name__)
