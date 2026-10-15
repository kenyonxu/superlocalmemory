# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Exact identity isolation for vectors, response IDs, boundaries and centroids."""
import inspect
import json
import uuid

import numpy as np

from superlocalmemory.optimize.cache.centroid_store import CentroidStore


class SpaceVectors:
    def __init__(self, db, identity):
        self.db, self.identity = db, identity

    def get_all_vectors(self, tenant_id):
        rows = self.db._db.execute(
            "SELECT entry_id, vector_blob, context_fp FROM llmcache_semantic_vectors "
            "WHERE tenant_id = ? AND model_name = ?",
            (tenant_id, self.identity),
        )
        return [(dict(row)["entry_id"], dict(row)["vector_blob"], dict(row)["context_fp"])
                for row in rows]


def prepare(cache, query_text, vector):
    if cache._anonymous_owner is not cache._embedder:
        cache._anonymous_space = "transient:" + uuid.uuid4().hex
        cache._anonymous_owner = cache._embedder
    identity = cache._anonymous_space
    if vector is None:
        if cache._embedder is None:
            return None
        embed = inspect.getattr_static(cache._embedder, "embed_with_identity", None)
        if embed is not None:
            vector, identity = cache._embedder.embed_with_identity(query_text)
        else:
            vector = cache._embedder(query_text)
            declared = getattr(cache._embedder, "space_id", None)
            if isinstance(declared, str) and declared:
                identity = declared
    if vector is None:
        return None
    if identity != cache._space_id:
        dimension = (json.loads(identity)["dimension"] if identity.startswith("{")
                     else cache._initial_dimension)
        cache._embed_dim = dimension
        cache._index.clear()
        cache._boundary_store._cache.clear()
        cache._centroid_store = CentroidStore(embedding_dimension=dimension)
        cache._space_id = identity
    vec = np.asarray(vector, dtype=np.float32)
    return vec if cache._accept_dimension(vec) else None


def persist_response(db, tenant_id, entry_id, response):
    body = (response.body if hasattr(response, "body")
            else response if isinstance(response, dict) else None)
    if body is None and hasattr(response, "body_bytes"):
        body = json.loads(response.body_bytes)
    if body is None:
        return
    db.set_with_entry_id(key=f"sem:{entry_id}", tenant_id=tenant_id,
                         value=json.dumps(body, separators=(",", ":")).encode(),
                         entry_id=entry_id, tags=["semantic"])
    db._db.execute("UPDATE llmcache_entries SET cache_tier = 'semantic' "
               "WHERE cache_key = ? AND tenant_id = ?", (f"sem:{entry_id}", tenant_id))
