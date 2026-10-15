# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Analytical and seeded regression fixtures for WP-12a mathematics."""
import math

import numpy as np
import pytest

from superlocalmemory.core.config import PolarQuantConfig
from superlocalmemory.math.fisher import FisherRaoMetric
from superlocalmemory.math.fisher_quantized import FRQADConfig, FRQADMetric
from superlocalmemory.math.hopfield import HopfieldConfig, ModernHopfieldNetwork
from superlocalmemory.math.turbo_quant import TurboQuantEncoder, TurboQuantResult


def test_eng05_variances_convert_to_standard_deviation():
    metric = FisherRaoMetric()
    assert metric.distance([0], [1], [0], [4]) == pytest.approx(math.sqrt(2) * math.log(2))
    assert metric.distance([0], [4], [2], [4]) == pytest.approx(math.sqrt(2) * math.acosh(1.25))
    assert metric.distance([0, 0], [1, 4], [0, 2], [4, 4]) == pytest.approx(
        math.hypot(math.sqrt(2) * math.log(2), math.sqrt(2) * math.acosh(1.25))
    )


def test_eng06_completion_retrieves_pattern_instead_of_centroid():
    memories = np.eye(768, dtype=np.float32)
    network = ModernHopfieldNetwork(HopfieldConfig(dimension=768))
    state = network.retrieve(memories[0], memories, max_iterations=3)
    assert state.attention_weights[0] > 0.99
    np.testing.assert_allclose(state.retrieved_pattern, memories[0], atol=1e-5)


def test_eng06_completion_survives_centroid_distractors():
    memories = np.zeros((129, 768), dtype=np.float32)
    memories[0, 0] = 1
    memories[1:, 0] = 0.5
    memories[1:, 1] = math.sqrt(0.75)
    query = memories[0].copy()
    network = ModernHopfieldNetwork(HopfieldConfig(dimension=768))
    result = network.retrieve(query, memories).retrieved_pattern
    assert np.argmax(memories @ result) == 0
    assert result[0] > 0.99


@pytest.mark.parametrize('seed', range(10))
def test_eng07_precision_penalty_is_monotone_for_fixed_gaussians(seed):
    rng = np.random.default_rng(seed)
    metric = FRQADMetric(FisherRaoMetric(), FRQADConfig())
    mean_a, mean_b = rng.normal(size=(2, 8))
    var_a, var_b = rng.uniform(0.05, 5, size=(2, 8))
    for query_width in (2, 4, 8, 32):
        distances = [metric.distance(mean_a, var_a, query_width, mean_b, var_b, bw)
                     for bw in (32, 8, 4, 2)]
        assert distances == sorted(distances)


def test_eng07_original_counterexample_no_longer_rewards_four_bits():
    metric = FRQADMetric(FisherRaoMetric(), FRQADConfig())
    assert metric.distance(np.zeros(1), np.array([2.]), 32, np.zeros(1), np.ones(1), 4) > (
        metric.distance(np.zeros(1), np.array([2.]), 32, np.zeros(1), np.ones(1), 32)
    )


def _encoder(tmp_path):
    path = tmp_path / 'rotation.npy'
    np.save(path, np.eye(768))
    return TurboQuantEncoder(PolarQuantConfig(dimension=768, rotation_matrix_path=str(path)))


def test_eng24_two_bits_use_four_levels_and_beat_sign_quantization(tmp_path):
    encoder = _encoder(tmp_path)
    rng = np.random.default_rng(2026)
    vectors = rng.normal(size=(160, 768))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    decoded = np.array([encoder.decode(encoder.encode(v, 2)) for v in vectors])
    sign_code = math.sqrt(2 / math.pi / 768) * np.sign(vectors)
    assert len(np.unique(decoded)) == 4
    assert np.mean(np.sum((vectors - decoded) ** 2, axis=1)) < (
        np.mean(np.sum((vectors - sign_code) ** 2, axis=1))
    )


def test_eng24_new_codec_version_preserves_old_blob_decode(tmp_path):
    encoder = _encoder(tmp_path)
    vector = np.zeros(768)
    vector[0] = 1
    encoded = encoder.encode(vector, 2)
    assert len(encoded.indices) == 768 // 4 + 3
    # Captured old 768-dimensional codebook values: old index 1, repeated.
    legacy = TurboQuantResult(1., b'TQ' + bytes([0x55] * 192), 2)
    decoded = encoder.decode(legacy)
    np.testing.assert_allclose(decoded, np.full(768, -0.028791179), atol=1e-7)


def test_eng06_beta_can_be_calibrated_for_unit_norm_patterns():
    network = ModernHopfieldNetwork(HopfieldConfig(dimension=3, beta=20.))
    state = network.retrieve(np.array([1., 0., 0.]), np.eye(3))
    np.testing.assert_allclose(state.retrieved_pattern, [1., 0., 0.], atol=1e-6)


@pytest.mark.parametrize('beta', [0., -1., float('inf'), float('nan')])
def test_eng06_invalid_beta_is_rejected(beta):
    with pytest.raises(ValueError):
        ModernHopfieldNetwork(HopfieldConfig(dimension=3, beta=beta))


@pytest.mark.parametrize('width', [2, 4, 8])
def test_eng24_actual_dimension_occupancy_and_distortion(tmp_path, width):
    encoder = _encoder(tmp_path)
    rng = np.random.default_rng(74)
    vectors = rng.normal(size=(200, 768))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    decoded = np.array([encoder.decode(encoder.encode(v, width)) for v in vectors])
    # All four/two-bit and sixteen/four-bit cells used; most 8-bit cells used.
    assert len(np.unique(decoded)) >= {2: 4, 4: 16, 8: 240}[width]
    assert np.mean(np.sum((vectors - decoded) ** 2, axis=1)) < {
        2: 0.12, 4: 0.012, 8: 0.0002,
    }[width]


def test_eng24_unknown_version_cannot_silently_decode(tmp_path):
    encoder = _encoder(tmp_path)
    bad = TurboQuantResult(1., b'TQ\x03' + bytes(192), 2)
    with pytest.raises(ValueError):
        encoder.decode(bad)
