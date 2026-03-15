# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Cross-Encoder Reranker.

Scores (query, fact) pairs through a cross-encoder in a single forward
pass. Lazy model loading, thread-safe via lock.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: MIT
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from superlocalmemory.storage.models import AtomicFact

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Rerank candidate facts using a local cross-encoder model.

    When the model is unavailable (missing package, download failure,
    offline environment), falls back to returning candidates in their
    original score order — never crashes.

    Args:
        model_name: HuggingFace cross-encoder model identifier.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
    ) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._loaded = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Load cross-encoder on first use (thread-safe)."""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return  # Double-check after acquiring lock
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self._model_name)
                logger.info("Cross-encoder loaded: %s", self._model_name)
            except ImportError:
                logger.warning(
                    "sentence-transformers not installed; "
                    "cross-encoder reranking disabled"
                )
            except OSError as exc:
                logger.warning(
                    "Failed to load cross-encoder %s: %s",
                    self._model_name,
                    exc,
                )
            finally:
                self._loaded = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rerank(
        self,
        query: str,
        candidates: list[tuple[AtomicFact, float]],
        top_k: int = 10,
    ) -> list[tuple[AtomicFact, float]]:
        """Rerank candidates by cross-encoder relevance.

        Each (query, fact.content) pair is scored in a single forward
        pass. Results are returned sorted by cross-encoder score.

        When the model is unavailable, returns candidates sorted by
        their existing score (graceful fallback).

        Args:
            query: User query text.
            candidates: List of (AtomicFact, score) tuples from the
                fusion stage.
            top_k: Maximum results to return.

        Returns:
            Top-k (AtomicFact, cross_encoder_score) tuples, sorted
            descending by cross-encoder score.
        """
        if not candidates:
            return []

        self._ensure_model()

        if self._model is None:
            # Fallback: keep existing score order
            sorted_cands = sorted(
                candidates, key=lambda x: x[1], reverse=True
            )
            return sorted_cands[:top_k]

        # Build (query, document) pairs for batch scoring
        pairs: list[tuple[str, str]] = [
            (query, fact.content) for fact, _ in candidates
        ]

        scores = self._model.predict(pairs)

        scored: list[tuple[AtomicFact, float]] = [
            (fact, float(score))
            for (fact, _), score in zip(candidates, scores)
        ]

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def score_pair(self, query: str, document: str) -> float:
        """Score a single (query, document) pair.

        Args:
            query: Query text.
            document: Document text.

        Returns:
            Relevance score (higher = more relevant). 0.0 if model
            is unavailable.
        """
        self._ensure_model()

        if self._model is None:
            return 0.0

        scores = self._model.predict([(query, document)])
        return float(scores[0])

    @property
    def is_available(self) -> bool:
        """Whether the cross-encoder model is loaded and ready."""
        self._ensure_model()
        return self._model is not None
