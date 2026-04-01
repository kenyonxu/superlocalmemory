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
import platform
import struct
import sys
import threading
from typing import Any

from superlocalmemory.storage.models import AtomicFact

logger = logging.getLogger(__name__)


def _detect_onnx_variant() -> str:
    """Auto-detect the best ONNX model variant for the current platform.

    Returns the file_name parameter for CrossEncoder model_kwargs.
    Platform detection:
    - macOS ARM64 (Apple Silicon): qint8_arm64
    - x86_64 with AVX2: quint8_avx2
    - Everything else: default model.onnx (float32, works everywhere)
    """
    arch = platform.machine().lower()
    is_64bit = struct.calcsize("P") * 8 == 64

    if sys.platform == "darwin" and arch in ("arm64", "aarch64"):
        return "onnx/model_qint8_arm64.onnx"

    if arch in ("x86_64", "amd64") and is_64bit:
        return "onnx/model_quint8_avx2.onnx"

    return "onnx/model.onnx"


class CrossEncoderReranker:
    """Rerank candidate facts using a local cross-encoder model.

    V3.3.2: Uses ONNX backend by default (~200MB) instead of full PyTorch
    (~1.5GB). Three-tier fallback: ONNX → PyTorch → no reranking.
    Auto-detects the optimal quantized ONNX variant per platform.

    When the model is unavailable (missing package, download failure,
    offline environment), falls back to returning candidates in their
    original score order — never crashes.

    Args:
        model_name: HuggingFace cross-encoder model identifier.
        backend: Inference backend. "onnx" for ONNX Runtime (light),
            "" for PyTorch (heavy). Default: "onnx".
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        backend: str = "onnx",
    ) -> None:
        self._model_name = model_name
        self._backend = backend
        self._model: Any = None
        self._loaded = False
        self._loading = False  # True while background load is in progress
        self._active_backend: str = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy loading (non-blocking)
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Trigger model load in background (non-blocking).

        On first call, starts loading in a background thread and returns
        immediately. The model becomes available for subsequent calls
        once loading completes. This prevents the 30s ONNX cold start
        from blocking the first recall request.

        Three-tier fallback:
        1. ONNX backend with platform-optimal quantization — ~100-200MB RAM
        2. PyTorch backend (requires torch) — ~1.5GB RAM
        3. No model (graceful degradation) — 0 RAM
        """
        if self._loaded:
            return

        with self._lock:
            if self._loaded or self._loading:
                return
            self._loading = True

        # Load in background thread so first recall isn't blocked
        loader = threading.Thread(
            target=self._load_model, daemon=True, name="ce-loader",
        )
        loader.start()

    def _load_model(self) -> None:
        """Actually load the model (runs in background thread)."""
        try:
            from sentence_transformers import CrossEncoder

            if self._backend == "onnx":
                try:
                    onnx_file = _detect_onnx_variant()
                    model = CrossEncoder(
                        self._model_name,
                        backend="onnx",
                        model_kwargs={"file_name": onnx_file},
                    )
                    self._model = model
                    self._active_backend = "onnx"
                    logger.info(
                        "Cross-encoder loaded (ONNX %s): %s",
                        onnx_file, self._model_name,
                    )
                except Exception as onnx_exc:
                    logger.info(
                        "ONNX backend unavailable (%s), falling back to PyTorch",
                        onnx_exc,
                    )
                    model = CrossEncoder(self._model_name)
                    self._model = model
                    self._active_backend = "pytorch"
                    logger.info(
                        "Cross-encoder loaded (PyTorch fallback): %s",
                        self._model_name,
                    )
            else:
                model = CrossEncoder(self._model_name)
                self._model = model
                self._active_backend = "pytorch"
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
            self._loading = False

    def _ensure_model_blocking(self) -> None:
        """Load model synchronously (blocks until ready).

        Used by warmup and is_available where we need the model NOW.
        """
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loading = True
        self._load_model()

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

        # Non-blocking: trigger background load if not yet started
        self._ensure_model()

        if self._model is None:
            # Model not loaded yet (still loading in background or failed).
            # Graceful fallback: return candidates sorted by existing score.
            # Next recall will use the model once it's ready.
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
        self._ensure_model_blocking()
        return self._model is not None
