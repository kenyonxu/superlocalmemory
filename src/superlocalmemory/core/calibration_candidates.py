# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Raw semantic candidates for floor calibration; always hydrate through visibility."""

from __future__ import annotations

from superlocalmemory.retrieval.query_embedding import embed_as_query


def raw_semantic(engine, question, top_k):
    retrieval = engine._retrieval_engine
    # RetrievalEngine holds its channels as named attributes (_semantic, _bm25,
    # ...); it has never had a `_channels` dict. 4.2.1 read one, and every
    # calibration on a real install died at 40/48 on the first absent-answer
    # sample because the stand-in engines in its tests invented the dict.
    channel = retrieval._semantic
    embedder = getattr(engine, "_embedder", None)
    if channel is None or embedder is None:
        return [], []
    vector = embed_as_query(embedder, question)
    if not vector:
        return [], []
    hits = channel.search(
        vector, engine.profile_id, top_k=top_k, include_global=False, include_shared=False
    )
    facts = engine._db.get_facts_by_ids(
        [fact_id for fact_id, _ in hits], profile_id=engine.profile_id
    )
    by_id = {fact.fact_id: fact for fact in facts}
    visible = [(by_id[fact_id], score) for fact_id, score in hits if fact_id in by_id]
    return [fact for fact, _ in visible], [max(0.0, 2 * score - 1) for _, score in visible]


def source_score(engine, question, source_id):
    """A positive label belongs to its source vector, never the best noise hit."""
    import numpy as np

    facts = engine._db.get_facts_by_ids([source_id], profile_id=engine.profile_id)
    fact = next((f for f in facts if f.fact_id == source_id), None)
    if fact is None or not fact.embedding:
        return []
    query = embed_as_query(engine._embedder, question)
    if not query or len(query) != len(fact.embedding):
        return []
    q, d = np.asarray(query), np.asarray(fact.embedding)
    norm = np.linalg.norm(q) * np.linalg.norm(d)
    if not np.isfinite(norm) or norm <= 0:
        return []
    return [max(0.0, min(1.0, float(np.dot(q, d) / norm)))]
