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