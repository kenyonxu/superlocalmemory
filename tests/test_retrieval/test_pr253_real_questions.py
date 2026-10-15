"""PR-253 #1: cached whole-word vocabulary cannot veto real questions."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.core.embedding_recipe import RecipeEmbedder, dense_text
from superlocalmemory.retrieval.query_embedding import QueryEmbedder

QUESTIONS = [
    "Varun Pratap Bhardwaj?", "Qualixar SuperLocalMemory AgentAssay?",
    "QueryEmbedder RecipeEmbedder SemanticChannel?", "Kubernetes kubectl kubeconfig?",
    "SAML OIDC PKCE?", "Pourquoi Qualixar ralentit?", "Wie funktioniert SuperLocalMemory?",
    "यह कैसे काम करता है?", "What is Qualixar used for?",
    "get_memory_recall_pipeline_v2", "getMemoryRecallPipelineV2",
]


@pytest.mark.parametrize("question", QUESTIONS)
def test_review_questions_reach_healthy_legacy_provider(question, monkeypatch):
    # Actual offline cached Nomic vocabulary, the reviewer's precise input.
    from huggingface_hub import try_to_load_from_cache

    from superlocalmemory.retrieval import query_lexicon
    cached = try_to_load_from_cache(
        "nomic-ai/nomic-embed-text-v1.5", "vocab.txt",
        cache_dir=os.environ.get("SLM_TEST_NOMIC_CACHE"),
    )
    vocab = (frozenset(Path(cached).read_text().splitlines())
             if isinstance(cached, str) else frozenset({"who", "owns", "how"}))
    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: vocab)
    native = SimpleNamespace(_config=EmbeddingConfig(), embed=Mock(return_value=[1.0, 0.0]))
    qe = QueryEmbedder(lambda: native)
    try:
        assert qe.embed(question, 0.5) == ([1.0, 0.0], None)
        native.embed.assert_called_once_with(question)
    finally:
        threads = qe.worker_threads()
        qe.close()
        for thread in threads:
            thread.join(1)


@pytest.mark.parametrize("identifier", [
    "getMemoryRecallPipelineV2", "SuperLocalMemoryVersion4125", "KubernetesAuthenticationV1",
])
def test_versioned_queries_and_documents_preserve_real_identifiers(identifier):
    provider = Mock()
    provider.embed.return_value = [1.0]
    embedder = RecipeEmbedder(provider)
    for text in (identifier, f"What does {identifier} do?"):
        assert dense_text(text) == text
        assert embedder.embed_query(text) == [1.0]
        provider.embed.assert_called_with("search_query: " + text)
        embedder.embed(text)
        provider.embed.assert_called_with("search_document: " + text)


def test_french_query_keeps_dense_channels_and_independent_lexical_lookup(monkeypatch):
    from superlocalmemory.retrieval import query_lexicon
    from superlocalmemory.retrieval.strategy import QueryStrategy
    from tests.test_retrieval.test_a_loading_embedder_does_not_hold_recall import _engine

    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: frozenset({"who", "owns", "how"}))
    native = SimpleNamespace(_config=EmbeddingConfig(), embed=Mock(return_value=[1.0, 0.0]))
    engine = _engine(native)
    statuses, dropped = {}, set()
    try:
        results = engine._run_channels(
            "Pourquoi Qualixar ralentit?", "default",
            QueryStrategy(query_type="factual", weights={}),
            channel_status=statuses, dropped_channels=dropped,
        )
        assert results["semantic"] == [("s1", 0.9)]
        assert results["bm25"] == [("exact", 3.0)]
        assert {"hopfield", "spreading_activation"} <= results.keys()
        assert not dropped
    finally:
        engine.close(wait=True)
