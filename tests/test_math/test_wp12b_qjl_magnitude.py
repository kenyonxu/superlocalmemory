"""Independent numerical oracles for QJL Definition 3.1, Equation 4."""

import math

import numpy as np
import pytest

from superlocalmemory.core.config import QJLConfig
from superlocalmemory.math.qjl import QJLEncoder


def test_hand_computed_correction_preserves_residual_norm():
    encoder = QJLEncoder(QJLConfig(projection_dim=4))
    # R = S / sqrt(m), matching the shipped projection normalization.
    encoder._R = np.array([[1., 0.], [0., 1.], [-1., 0.], [0., -1.]]) / 2
    residual = np.array([3., 4.])
    query = np.array([2., 1.])
    # signs=(+,+,-,-); dot(Rq,signs)=3; ||r||=5.
    expected = math.sqrt(math.pi / 8) * 5 * 3
    estimate = encoder.estimate_correction(query, encoder.encode_residual(residual))
    assert estimate == pytest.approx(expected)
    scaled = encoder.encode_residual(2 * residual)
    assert encoder.estimate_correction(query, scaled) == pytest.approx(2 * expected)


def test_zero_residual_has_zero_correction():
    encoder = QJLEncoder(QJLConfig(projection_dim=9))
    assert encoder.estimate_correction(np.ones(3), encoder.encode_residual(np.zeros(3))) == 0


def test_legacy_signs_are_not_assumed_to_have_unit_magnitude(caplog):
    encoder = QJLEncoder(QJLConfig(projection_dim=9))
    assert encoder.estimate_correction(np.ones(3), b"\xff\xff") == 0
    assert "lack residual magnitude" in caplog.text


def test_truncated_payload_is_rejected():
    encoder = QJLEncoder(QJLConfig(projection_dim=9))
    payload = encoder.encode_residual(np.ones(3))
    with pytest.raises(ValueError, match="length"):
        encoder.estimate_correction(np.ones(3), payload[:-1])


@pytest.mark.parametrize("scale", [0.01, 1., 8.])
def test_aligned_query_is_unbiased_over_seeds(scale):
    query = np.array([1., 0., 0.])
    estimates = []
    for seed in range(150):
        encoder = QJLEncoder(QJLConfig(projection_dim=256, seed=seed))
        estimates.append(encoder.estimate_correction(query, encoder.encode_residual(query * scale)))
    assert np.mean(estimates) == pytest.approx(scale, rel=0.02)
