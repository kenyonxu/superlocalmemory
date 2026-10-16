# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Versioned Nomic prompts: preserve v1 documents, stage identifier-preserving v2.

Query preprocessing never removes valid text, including code and hash IDs.
The document transform is part of the stored space identity; changes require
an explicit staged migration, never an in-place reinterpretation of vectors.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

TASK_SUFFIX = "@retrieval-v2"
LEGACY_TASK_SUFFIX = "@retrieval-v1"
TASK_SUFFIXES = (LEGACY_TASK_SUFFIX, TASK_SUFFIX)
NOMIC = "nomic-embed-text-v1.5"


def task_model(model: str) -> str:
    base = model
    for suffix in TASK_SUFFIXES:
        base = base.removesuffix(suffix)
    if base.rsplit("/", 1)[-1] != NOMIC:
        raise ValueError("Task prefixes are supported only for Nomic v1.5")
    return base + TASK_SUFFIX


def dense_text(text: str) -> str:
    """Preserve all meaningful tokens; vocabulary/entropy cannot prove gibberish."""
    return " ".join(text.split())


class RecipeEmbedder:
    """Add prompts once at the provider boundary, including bulk/catch-up writes."""

    def __init__(self, delegate: Any, model_name: str = "") -> None:
        self._delegate = delegate
        self.model_name = model_name or task_model("nomic-ai/" + NOMIC)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _document(self, text):
        if self.model_name.endswith(LEGACY_TASK_SUFFIX):
            from superlocalmemory.core.embedding_recipe_v1 import document_text
            return document_text(text)
        return dense_text(text)

    def embed(self, text: str) -> list[float] | None:
        content = self._document(text)
        return self._delegate.embed("search_document: " + content)

    def embed_query(self, text: str) -> list[float] | None:
        content = dense_text(text)
        if not content:
            return None  # empty input has no tokens; every real query is retained
        return self._delegate.embed("search_query: " + content)

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        return self._delegate.embed_batch(["search_document: " + self._document(t) for t in texts])


def build_with_recipe(config: Any, build: Any) -> Any:
    model = config.embedding.model_name
    if not model.endswith(TASK_SUFFIXES):
        if "@retrieval-" in model:
            raise ValueError("Unsupported embedding recipe; upgrade or roll back the stored space")
        return build(config)
    task_model(model)  # refuse an unsupported suffix rather than load the wrong model
    if config.embedding.provider not in {"", "sentence-transformers"}:
        raise ValueError("Nomic retrieval recipes require the sentence-transformers provider")
    base = replace(
        config.embedding,
        model_name=model.rsplit("@retrieval-", 1)[0],
        provider="sentence-transformers", api_endpoint="", api_key="", deployment_name="",
    )
    # A shallow copy keeps all operational configuration on the private target.
    import copy

    target = copy.copy(config)
    target.embedding = base
    delegate = build(target)
    return RecipeEmbedder(delegate, model_name=model) if delegate is not None else None
