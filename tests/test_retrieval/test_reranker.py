# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Tests for superlocalmemory.retrieval.reranker — Cross-Encoder Reranker.

Covers:
  - Lazy model loading (mocked sentence_transformers import)
  - rerank() with model available -> scored and sorted
  - rerank() with model unavailable -> fallback to existing order
  - rerank() with empty candidates
  - score_pair() with and without model
  - is_available property
  - Thread-safe double-check loading pattern
  - ImportError handling (sentence-transformers not installed)
  - OSError handling (model download failure)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from superlocalmemory.retrieval.reranker import CrossEncoderReranker
from superlocalmemory.storage.models import AtomicFact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(fact_id: str, content: str = "") -> AtomicFact:
    return AtomicFact(
        fact_id=fact_id, memory_id="m0",
        content=content or f"Content for {fact_id}",
    )


def _make_candidates(n: int = 3) -> list[tuple[AtomicFact, float]]:
    return [
        (_make_fact(f"f{i}", f"Document {i}"), 0.5 - i * 0.1)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

class TestModelLoading:
    def test_model_not_loaded_until_first_use(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        assert reranker._loaded is False
        assert reranker._model is None

    @patch("superlocalmemory.retrieval.reranker.CrossEncoder", create=True)
    def test_lazy_load_success(self, mock_ce_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_ce_class.return_value = mock_model

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(CrossEncoder=mock_ce_class)},
        ):
            reranker = CrossEncoderReranker("test-model")
            reranker._ensure_model()

        assert reranker._loaded is True

    def test_import_error_graceful(self) -> None:
        """When sentence_transformers is not installed, model stays None."""
        reranker = CrossEncoderReranker("fake-model")
        # Force _ensure_model with import failure
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            reranker._ensure_model()
        assert reranker._loaded is True
        assert reranker._model is None

    def test_os_error_graceful(self) -> None:
        """When model download fails, model stays None."""
        reranker = CrossEncoderReranker("fake-model")
        mock_st = MagicMock()
        mock_st.CrossEncoder.side_effect = OSError("Download failed")
        with patch.dict("sys.modules", {"sentence_transformers": mock_st}):
            reranker._ensure_model()
        assert reranker._loaded is True
        assert reranker._model is None


# ---------------------------------------------------------------------------
# rerank() — model available
# ---------------------------------------------------------------------------

class TestRerankWithModel:
    def _make_reranker_with_model(self) -> CrossEncoderReranker:
        reranker = CrossEncoderReranker("fake-model")
        mock_model = MagicMock()
        # predict returns scores in reverse order to test sorting
        mock_model.predict.return_value = [0.1, 0.5, 0.9]
        reranker._model = mock_model
        reranker._loaded = True
        return reranker

    def test_rerank_sorts_by_cross_encoder_score(self) -> None:
        reranker = self._make_reranker_with_model()
        candidates = _make_candidates(3)
        results = reranker.rerank("query", candidates, top_k=10)
        # Scores are [0.1, 0.5, 0.9] -> f2 (0.9) should be first
        assert results[0][0].fact_id == "f2"
        assert results[0][1] == pytest.approx(0.9)

    def test_rerank_respects_top_k(self) -> None:
        reranker = self._make_reranker_with_model()
        candidates = _make_candidates(3)
        results = reranker.rerank("query", candidates, top_k=2)
        assert len(results) == 2

    def test_rerank_passes_correct_pairs(self) -> None:
        reranker = self._make_reranker_with_model()
        candidates = [
            (_make_fact("f1", "doc one"), 0.5),
            (_make_fact("f2", "doc two"), 0.3),
        ]
        reranker._model.predict.return_value = [0.8, 0.4]
        reranker.rerank("my query", candidates)
        # Check that predict was called with (query, content) pairs
        call_args = reranker._model.predict.call_args[0][0]
        assert call_args[0] == ("my query", "doc one")
        assert call_args[1] == ("my query", "doc two")


# ---------------------------------------------------------------------------
# rerank() — model unavailable (fallback)
# ---------------------------------------------------------------------------

class TestRerankFallback:
    def _make_reranker_no_model(self) -> CrossEncoderReranker:
        reranker = CrossEncoderReranker("fake-model")
        reranker._model = None
        reranker._loaded = True
        return reranker

    def test_fallback_returns_sorted_by_existing_score(self) -> None:
        reranker = self._make_reranker_no_model()
        candidates = [
            (_make_fact("f1"), 0.3),
            (_make_fact("f2"), 0.9),
            (_make_fact("f3"), 0.6),
        ]
        results = reranker.rerank("query", candidates)
        assert results[0][0].fact_id == "f2"
        assert results[1][0].fact_id == "f3"

    def test_fallback_respects_top_k(self) -> None:
        reranker = self._make_reranker_no_model()
        candidates = _make_candidates(5)
        results = reranker.rerank("query", candidates, top_k=2)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# rerank() — empty candidates
# ---------------------------------------------------------------------------

class TestRerankEmpty:
    def test_empty_candidates_returns_empty(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        reranker._model = MagicMock()
        reranker._loaded = True
        assert reranker.rerank("query", []) == []


# ---------------------------------------------------------------------------
# score_pair()
# ---------------------------------------------------------------------------

class TestScorePair:
    def test_score_pair_with_model(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.75]
        reranker._model = mock_model
        reranker._loaded = True

        score = reranker.score_pair("query", "document text")
        assert score == pytest.approx(0.75)
        mock_model.predict.assert_called_once_with([("query", "document text")])

    def test_score_pair_without_model(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        reranker._model = None
        reranker._loaded = True
        assert reranker.score_pair("query", "doc") == 0.0


# ---------------------------------------------------------------------------
# is_available property
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_available_when_model_loaded(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        reranker._model = MagicMock()
        reranker._loaded = True
        assert reranker.is_available is True

    def test_not_available_when_model_none(self) -> None:
        reranker = CrossEncoderReranker("fake-model")
        reranker._model = None
        reranker._loaded = True
        assert reranker.is_available is False
