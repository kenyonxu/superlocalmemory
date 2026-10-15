"""Prefixes/normalization are a different space, never an in-place upgrade."""

from dataclasses import replace
from unittest.mock import Mock

from superlocalmemory.core.config import EmbeddingConfig
from superlocalmemory.storage.embedding_spaces import same_space, signature_of


def test_task_prompts_change_space_and_leave_legacy_untouched():
    from superlocalmemory.core.embedding_recipe import RecipeEmbedder, task_model

    legacy = EmbeddingConfig(provider="sentence-transformers")
    upgraded = replace(legacy, model_name=task_model(legacy.model_name))
    assert not same_space(signature_of(legacy), signature_of(upgraded))
    worker = Mock()
    embedder = RecipeEmbedder(worker)
    embedder.embed("The rollback owner is Alice.")
    embedder.embed_query("Who owns rollback?")
    assert worker.embed.call_args_list[0].args == ("search_document: The rollback owner is Alice.",)
    assert worker.embed.call_args_list[1].args == ("search_query: Who owns rollback?",)


def test_v2_keeps_code_and_hash_identifiers_in_dense_and_lexical_text():
    from superlocalmemory.core.embedding_recipe import RecipeEmbedder

    content = "Beacon uses blue green deployment. " + "a24b97ed6ca5f098174d653abc992fde " * 16
    worker = Mock()
    embedder = RecipeEmbedder(worker)
    embedder.embed(content)
    sent = worker.embed.call_args.args[0]
    assert "a24b97" in sent
    assert "Beacon uses blue green deployment" in sent
    assert "a24b97" in content
    embedder.embed_query("a24b97ed6ca5f098174d653abc992fde")
    worker.embed.assert_called_with("search_query: a24b97ed6ca5f098174d653abc992fde")


def test_daemon_reports_the_versioned_space_not_the_bare_delegate():
    from types import SimpleNamespace

    from superlocalmemory.core.daemon_text_embedder import embedder_identity
    from superlocalmemory.core.embedding_recipe import RecipeEmbedder, task_model

    delegate = SimpleNamespace(_config=EmbeddingConfig(), dimension=768)
    assert embedder_identity(RecipeEmbedder(delegate)) == (
        task_model(delegate._config.model_name),
        768,
    )
