# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Fisher-Rao Quantization-Aware Distance (FRQAD).

Ranking distance = Fisher-Rao distance of the observed Gaussians plus an
explicit bounded precision penalty, kappa * (1 - bit_width / 32) per operand.
For fixed means/variances, reducing either operand's precision cannot improve
similarity. Float32 pairs retain the base distance. This ranking distance is
not a metric: low-precision self-comparisons include a reliability penalty.

Variance inflation remains available as an uncertainty-model helper; it is
not used for ranking because changing uncertainty can reduce Gaussian distance.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from superlocalmemory.math.fisher import FisherRaoMetric

logger = logging.getLogger(__name__)

# Valid bit-widths aligned with Phase B/D (no 16-bit format exists).
_VALID_BIT_WIDTHS: frozenset[int] = frozenset({2, 4, 8, 32})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FRQADConfig:
    """Configuration for FRQAD metric.

    Attributes:
        kappa:            Precision penalty strength (0 disables the penalty).
                          Also the legacy uncertainty helper's exponent.
        temperature:      Softmax temperature for similarity conversion.
        enabled:          When False, fall back to base Fisher-Rao.
        variance_floor:   Minimum per-dimension variance after inflation.
        variance_ceiling: Maximum per-dimension variance after inflation.
    """

    kappa: float = 0.5
    temperature: float = 15.0
    enabled: bool = True
    variance_floor: float = 0.05
    variance_ceiling: float = 10.0


# ---------------------------------------------------------------------------
# FRQAD Metric
# ---------------------------------------------------------------------------


class FRQADMetric:
    """Fisher-Rao Quantization-Aware Distance metric.

    Adds a monotone reliability penalty to the observed Fisher-Rao distance.
    This is a ranking distance, rather than a metric on quantized embeddings.

    Self-consistency property:
        FRQAD(32bit, 32bit) == FisherRao(same pair) within float epsilon.
    """

    __slots__ = ("_base", "_config")

    def __init__(
        self,
        base_metric: FisherRaoMetric,
        config: FRQADConfig,
    ) -> None:
        """Construct FRQAD metric.

        Args:
            base_metric: Existing Fisher-Rao metric instance.
            config:      FRQAD configuration.

        Raises:
            ValueError: On invalid configuration values.
        """
        # Validate config ranges (HR-04)
        if not 0.0 <= config.kappa <= 2.0:
            raise ValueError(
                f"kappa must be in [0.0, 2.0], got {config.kappa}"
            )
        if not 1.0 <= config.temperature <= 100.0:
            raise ValueError(
                f"temperature must be in [1.0, 100.0], got {config.temperature}"
            )
        if not 0.001 <= config.variance_floor <= 1.0:
            raise ValueError(
                f"variance_floor must be in [0.001, 1.0], got {config.variance_floor}"
            )
        if not 1.0 <= config.variance_ceiling <= 100.0:
            raise ValueError(
                f"variance_ceiling must be in [1.0, 100.0], got {config.variance_ceiling}"
            )

        self._base = base_metric
        self._config = config

    # ------------------------------------------------------------------
    # Quantization variance inflation
    # ------------------------------------------------------------------

    def quantization_variance(
        self,
        base_variance: NDArray[np.floating],
        bit_width: int,
    ) -> NDArray[np.floating]:
        """Inflate variance to model quantization uncertainty.

        HR-02: NEVER decreases base variance (scale >= 1.0 since bw <= 32).

        Args:
            base_variance: Per-dimension variance vector (strictly positive).
            bit_width:     Embedding precision in bits.

        Returns:
            Quantization-aware variance, clamped to [floor, ceiling].
        """
        if bit_width not in _VALID_BIT_WIDTHS:
            logger.warning(
                "Unknown bit_width %d, treating as 32 (no penalty)", bit_width,
            )
            return np.array(base_variance, dtype=np.float64)

        if bit_width >= 32:
            return np.array(base_variance, dtype=np.float64)

        # V3.3.26: MULTIPLICATIVE variance inflation (Paper 3, Equation 2).
        # sigma²_eff = sigma²_obs * (32 / bit_width) ^ kappa
        # When bw=32: scale=1.0 (no change). When bw=4: scale=2.83x (kappa=0.5).
        # This is MORE novel and MORE aggressive than additive Delta²/12.
        scale = (32.0 / bit_width) ** self._config.kappa
        sigma_inflated = np.asarray(base_variance, dtype=np.float64) * scale

        return np.clip(sigma_inflated, self._config.variance_floor, self._config.variance_ceiling)

    # ------------------------------------------------------------------
    # Core distance (THE novel contribution)
    # ------------------------------------------------------------------

    def distance(
        self,
        mu_a: NDArray[np.floating],
        var_a: NDArray[np.floating],
        bw_a: int,
        mu_b: NDArray[np.floating],
        var_b: NDArray[np.floating],
        bw_b: int,
    ) -> float:
        """FRQAD distance between mixed-precision embeddings.

        Keeps observed variance fixed and adds a bounded precision penalty.
        At fixed Gaussian parameters this is monotone in each bit-width.

        Args:
            mu_a:  Mean of embedding A.
            var_a: Per-dimension variance of A.
            bw_a:  Bit-width of A.
            mu_b:  Mean of embedding B.
            var_b: Per-dimension variance of B.
            bw_b:  Bit-width of B.

        Returns:
            Non-negative ranking distance (base geodesic plus reliability penalty).
        """
        base = self._base.distance(
            np.asarray(mu_a, dtype=np.float64).tolist(),
            np.asarray(var_a, dtype=np.float64).tolist(),
            np.asarray(mu_b, dtype=np.float64).tolist(),
            np.asarray(var_b, dtype=np.float64).tolist(),
        )
        if not self._config.enabled:
            return base
        # Gaussian uncertainty is not a precision penalty: moving variances
        # closer reduces the geodesic (Pinele et al., doi:10.3390/e22040404;
        # arXiv:2604.04514, Definition 4.2 does not imply Proposition 4.3).
        # https://github.com/geomstats/geomstats/blob/main/geomstats/information_geometry/normal.py,
        # checked 2026-10-11). This bounded ranking feature is an SLM policy,
        # not a paper-derived quantization-noise model or a metric axiom.
        widths = (bw if bw in _VALID_BIT_WIDTHS else 32 for bw in (bw_a, bw_b))
        penalty = sum(1.0 - bw / 32.0 for bw in widths) * self._config.kappa
        return base + penalty

    # ------------------------------------------------------------------
    # Similarity (exponential kernel)
    # ------------------------------------------------------------------

    def similarity(
        self,
        mu_a: NDArray[np.floating],
        var_a: NDArray[np.floating],
        bw_a: int,
        mu_b: NDArray[np.floating],
        var_b: NDArray[np.floating],
        bw_b: int,
    ) -> float:
        """Convert FRQAD distance to similarity in [0, 1].

        sim = clamp(exp(-distance / temperature), 0, 1)

        HR-03: No NaN, no negative, no > 1.
        """
        d = self.distance(mu_a, var_a, bw_a, mu_b, var_b, bw_b)
        sim = math.exp(-d / self._config.temperature)
        return max(0.0, min(1.0, sim))

    # ------------------------------------------------------------------
    # Batch similarity
    # ------------------------------------------------------------------

    def batch_similarity(
        self,
        query_mu: NDArray[np.floating],
        query_var: NDArray[np.floating],
        query_bw: int,
        candidates: list[tuple[str, NDArray[np.floating], NDArray[np.floating], int]],
    ) -> list[tuple[str, float]]:
        """Score a batch of candidate memories against a query.

        Args:
            query_mu:   Query embedding mean.
            query_var:  Query embedding variance.
            query_bw:   Query bit-width (typically 32).
            candidates: List of (fact_id, mu, var, bit_width) tuples.

        Returns:
            List of (fact_id, similarity) sorted descending by score.
        """
        results: list[tuple[str, float]] = []
        for fact_id, mu, var, bw in candidates:
            sim = self.similarity(query_mu, query_var, query_bw, mu, var, bw)
            results.append((fact_id, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results
