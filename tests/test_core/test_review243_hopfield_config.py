"""PR-243: beta must be usable through the public saved configuration."""

import json
import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from superlocalmemory.core.config import HopfieldConfig, SLMConfig
from superlocalmemory.core.engine_wiring import _init_hopfield_channel
from superlocalmemory.math.hopfield import ModernHopfieldNetwork
from superlocalmemory.storage.models import Mode


def test_public_hopfield_beta_controls_unit_pattern_attention():
    network = ModernHopfieldNetwork(HopfieldConfig(dimension=4, beta=2.0))
    expected = [0.7112345942275938] + [0.09625513525746872] * 3
    np.testing.assert_allclose(network.update(np.eye(4)[0], np.eye(4)), expected, atol=1e-7)


def test_beta_loaded_from_json_reaches_production_channel(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "a", "base_dir": str(tmp_path),
                               "hopfield": {"dimension": 4, "beta": 8.0}}))
    config = SLMConfig.load(path)
    channel = _init_hopfield_channel(MagicMock(), MagicMock(), config)
    assert channel is not None
    expected = math.exp(8.0) / (math.exp(8.0) + 3.0)
    scores = channel._hopfield.attention_scores(np.eye(4)[0], np.eye(4))
    assert scores[0] == pytest.approx(expected)


def test_public_config_saves_and_reloads_beta_and_other_hopfield_options(tmp_path):
    path = tmp_path / "config.json"
    config = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    config.hopfield = HopfieldConfig(dimension=4, beta=8.0, prefilter_candidates=300)
    config.save(path)
    assert json.loads(path.read_text())["hopfield"]["beta"] == 8.0
    assert SLMConfig.load(path).hopfield == config.hopfield


@pytest.mark.parametrize("beta", [None, 2.0])
def test_hand_edited_hopfield_section_round_trips_with_unknown_keys(tmp_path, beta):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "a", "hopfield": {
        "beta": beta, "dimension": 4, "future_option": "keep",
    }}))
    config = SLMConfig.load(path)
    assert config.hopfield.beta == beta
    config.save(path)
    assert json.loads(path.read_text())["hopfield"]["future_option"] == "keep"
    assert SLMConfig.load(path).hopfield == config.hopfield


@pytest.mark.parametrize("beta", [0.0, -1.0, float("inf"), float("nan"), "invalid", True])
def test_invalid_public_beta_is_rejected(beta):
    with pytest.raises(ValueError, match="beta must be finite and positive"):
        HopfieldConfig(beta=beta)


@pytest.mark.parametrize("section", [{"beta": 0}, {"beta": "invalid"}, []])
def test_invalid_saved_hopfield_config_warns_and_uses_defaults(tmp_path, caplog, section):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "a", "hopfield": section}))
    loaded = SLMConfig.load(path)
    assert loaded.hopfield == HopfieldConfig()
    assert "Ignoring invalid hopfield config" in caplog.text


def test_legacy_config_keeps_unit_norm_default(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"mode": "a"}')
    config = SLMConfig.load(path)
    assert config.hopfield.beta is None
    scores = ModernHopfieldNetwork(config.hopfield).attention_scores(
        np.eye(768)[0], np.eye(768),
    )
    assert scores[0] > 0.999
