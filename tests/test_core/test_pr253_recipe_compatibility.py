"""Changing document preprocessing needs a new space, with the old writer frozen."""
from unittest.mock import Mock

from superlocalmemory.core.embedding_recipe import RecipeEmbedder, task_model


def test_identifier_preserving_recipe_uses_a_new_space():
    assert task_model("nomic-ai/nomic-embed-text-v1.5").endswith("@retrieval-v2")
    assert task_model("nomic-ai/nomic-embed-text-v1.5@retrieval-v1").endswith("@retrieval-v2")


def test_existing_retrieval_v1_documents_keep_the_original_vector_transform():
    delegate = Mock()
    embedder = RecipeEmbedder(delegate, "nomic-ai/nomic-embed-text-v1.5@retrieval-v1")
    embedder.embed("What does getMemoryRecallPipelineV2 do?")
    delegate.embed.assert_called_with("search_document: What does do?")
    embedder.embed_batch(["getMemoryRecallPipelineV2"])
    delegate.embed_batch.assert_called_with(["search_document: "])


def test_both_recipe_queries_keep_valid_hex_code_identifiers():
    identifier = "a24b97ed6ca5f098174d653abc992fde"
    for suffix in ("@retrieval-v1", "@retrieval-v2"):
        delegate = Mock()
        delegate.embed.return_value = [1.0]
        embedder = RecipeEmbedder(delegate, "nomic-ai/nomic-embed-text-v1.5" + suffix)
        assert embedder.embed_query(identifier) == [1.0]
        delegate.embed.assert_called_with("search_query: " + identifier)
