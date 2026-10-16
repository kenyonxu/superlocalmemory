# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Exercise the production semantic channel/floor on a fresh canonical SLM DB."""

from __future__ import annotations

import os
from pathlib import Path


def recall_measure(folder, docs, queries, document_vectors, query_vectors, floor):
    os.environ["SLM_DATA_DIR"] = str(Path(folder).resolve())
    from superlocalmemory.core.config import RetrievalConfig
    from superlocalmemory.retrieval.engine import RetrievalEngine
    from superlocalmemory.retrieval.semantic_channel import SemanticChannel
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord

    path = Path(folder) / "memory.db"
    if path.exists():
        raise ValueError("Synthetic measurement requires a fresh store")
    db = DatabaseManager(path)
    db.initialize(schema)
    for i, content in enumerate(docs):
        memory = MemoryRecord(memory_id=f"m{i}", content=content)
        db.store_memory(memory)
        db.store_fact(
            AtomicFact(
                fact_id=f"f{i}",
                memory_id=memory.memory_id,
                content=content,
                embedding=document_vectors[i].tolist(),
                confidence=0.99,
            )
        )
    vectors = dict(zip(queries, (v.tolist() for v in query_vectors), strict=True))

    class EmbeddedQueries:
        def embed(self, text):
            return vectors[text]
    config = RetrievalConfig(min_semantic_evidence=floor, use_cross_encoder=False)
    config.enable_community_context = False
    engine = RetrievalEngine(
        db=db, config=config, embedder=EmbeddedQueries(), channels={"semantic": SemanticChannel(db)}
    )
    rows = []
    try:
        for query in queries:
            response = engine.recall(query, "default", limit=5)
            rows.append(
                {
                    "ids": [r.fact.fact_id for r in response.results],
                    "no_confident_match": response.no_confident_match,
                }
            )
    finally:
        engine.close()
        db.close()
    return rows
