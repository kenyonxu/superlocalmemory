# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Bound each embedding-worker request while preserving long-source coverage."""

from __future__ import annotations

import math

MAX_EMBED_CHARS = 4000


def embed_source(embedder, text: str):
    if embedder is None:
        return None
    if len(text) <= MAX_EMBED_CHARS:
        return embedder.embed(text)
    vectors = []
    for start in range(0, len(text), MAX_EMBED_CHARS):
        vector = embedder.embed(text[start : start + MAX_EMBED_CHARS])
        if vector is None:
            return None
        vectors.append(vector)
    if not vectors:
        return None
    if len({len(v) for v in vectors}) != 1:
        raise ValueError("Embedding dimensions changed within one source")
    mean = [sum(values) / len(vectors) for values in zip(*vectors)]
    norm = math.sqrt(sum(value * value for value in mean))
    return [value / norm for value in mean] if norm else mean
