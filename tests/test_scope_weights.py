"""Tests for ScopeWeights configuration and retrieval engine integration."""

from superlocalmemory.core.config import SLMConfig, ScopeWeights


def test_scope_weights_defaults():
    sw = ScopeWeights()
    assert sw.personal == 1.0
    assert sw.shared == 0.7
    assert sw.global_ == 0.5


def test_scope_weights_custom():
    sw = ScopeWeights(personal=1.2, shared=0.8, global_=0.6)
    assert sw.personal == 1.2
    assert sw.shared == 0.8
    assert sw.global_ == 0.6


def test_scope_weights_as_dict():
    sw = ScopeWeights()
    d = sw.as_dict()
    assert d == {"personal": 1.0, "shared": 0.7, "global": 0.5}


def test_slmconfig_has_scope_weights():
    config = SLMConfig.default()
    assert hasattr(config, "scope_weights")
    assert config.scope_weights.personal == 1.0
    assert config.scope_weights.global_ == 0.5


def test_scope_weights_validation():
    import pytest
    with pytest.raises(ValueError, match="non-negative"):
        ScopeWeights(personal=-0.1)


def test_retrieval_engine_uses_scope_weights():
    """RetrievalEngine reads scope weights from ScopeWeights config."""
    from superlocalmemory.core.config import ScopeWeights
    from unittest.mock import MagicMock
    from superlocalmemory.retrieval.engine import RetrievalEngine

    sw = ScopeWeights(personal=1.5, shared=0.3, global_=0.1)

    channels = {name: MagicMock() for name in
                ["semantic", "bm25", "entity_graph", "temporal", "hopfield", "spreading_activation"]}
    for ch in channels.values():
        ch.search.return_value = []
        ch.ensure_loaded = MagicMock()

    db = MagicMock()
    db.execute.return_value = []
    db.get_all_bm25_tokens.return_value = {}
    db.get_all_facts.return_value = []

    config = MagicMock()
    config.rrf_k = 15
    config.disabled_channels = []
    config.use_cross_encoder = False

    engine = RetrievalEngine(
        db=db, config=config, channels=channels, scope_weights=sw,
    )
    assert engine._scope_weights.personal == 1.5
    assert engine._scope_weights.global_ == 0.1


import json


def test_scope_weights_persist_load_save(tmp_path):
    """ScopeWeights round-trips through JSON config."""
    config = SLMConfig.default()
    config.scope_weights = ScopeWeights(personal=1.3, shared=0.6, global_=0.4)

    config_path = tmp_path / "config.json"
    config.base_dir = tmp_path
    config.save(config_path)

    data = json.loads(config_path.read_text())
    assert "scope_weights" in data
    assert data["scope_weights"]["personal"] == 1.3
    assert data["scope_weights"]["global_"] == 0.4

    loaded = SLMConfig.load(config_path)
    assert loaded.scope_weights.personal == 1.3
    assert loaded.scope_weights.shared == 0.6
    assert loaded.scope_weights.global_ == 0.4