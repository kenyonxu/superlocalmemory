# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""QJL (Quantized Johnson-Lindenstrauss) 1-bit residual correction.

Encodes the residual between original and PolarQuant-decoded embedding
as 1-bit sign projections. At query time, the asymmetric estimator
provides an unbiased correction to improve approximate similarity.

Pipeline:
  1. Random projection: projected = R @ residual  (m x d matrix)
  2. Sign quantize: bits = (projected > 0)
  3. Store a versioned residual norm followed by packed bits

Query-time correction:
  1. Project query: q_proj = R @ query
  2. Unpack signs to +/-1
  3. Estimate: sqrt(pi/(2*m)) * ||residual|| * dot(q_proj, signs)
     (R = S/sqrt(m), so this is Equation 4's sqrt(pi/2)/m scaling.)

HR-07: QJL is OPTIONAL -- system works without it.
HR-08: No new pip dependencies (numpy + stdlib only).

References:
  - Zandieh A et al. (2025). QJL: 1-Bit Quantized JL Transform for KNN.
    AAAI 2025, arXiv 2406.03482.
  - TurboQuant (ICLR 2026, arXiv 2504.19874).

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import math
import struct
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from superlocalmemory.core.config import QJLConfig

logger = logging.getLogger(__name__)
_HEADER = struct.Struct("<4sd")
_VERSION = b"QJL1"


@lru_cache(maxsize=1)
def _warn_legacy() -> None:
    logger.warning("Legacy QJL signs lack residual magnitude; recompress to enable correction")


def _unpack_payload(payload: bytes, m: int) -> tuple[float, bytes]:
    size = (m + 7) // 8
    if len(payload) == size:
        _warn_legacy()
        return 0.0, payload
    if len(payload) != _HEADER.size + size:
        raise ValueError("Invalid QJL payload length")
    version, norm = _HEADER.unpack_from(payload)
    if version != _VERSION or not math.isfinite(norm) or norm < 0:
        raise ValueError("Invalid QJL payload header")
    return norm, payload[_HEADER.size:]


class QJLEncoder:
    """1-bit random projection encoder for residual correction.

    The projection matrix R is (m x d) where m = projection_dim.
    Entries are i.i.d. N(0, 1/m) -- the Johnson-Lindenstrauss property
    preserves inner products in expectation.

    Lazy initialization: the projection matrix is created on first use
    to avoid allocating memory if QJL is never invoked.
    """

    __slots__ = ("_config", "_R")

    def __init__(self, config: QJLConfig) -> None:
        self._config = config
        self._R: NDArray | None = None

    def _ensure_projection(self, d: int) -> None:
        """Create or verify the random projection matrix.

        Matrix shape: (projection_dim, d).
        Scaling: 1/sqrt(m) for unbiased estimation.
        """
        if self._R is not None and self._R.shape[1] == d:
            return
        rng = np.random.default_rng(self._config.seed)
        m = self._config.projection_dim
        self._R = rng.standard_normal((m, d)) / math.sqrt(m)

    def encode_residual(self, residual: NDArray) -> bytes:
        """Encode a residual vector into 1-bit sign projections.

        Args:
            residual: Difference between original and decoded embedding.

        Returns:
            QJL1 header, float64 residual norm, and ceil(m/8) sign bytes.
        """
        self._ensure_projection(len(residual))
        projected = self._R @ residual
        bits = (projected > 0).astype(np.uint8)
        norm = float(np.linalg.norm(residual))
        if not math.isfinite(norm):
            raise ValueError("QJL residual must be finite")
        return _HEADER.pack(_VERSION, norm) + np.packbits(bits).tobytes()

    def estimate_correction(self, query: NDArray, qjl_bits: bytes) -> float:
        """Estimate inner product correction from QJL bits.

        This is the asymmetric estimator from the QJL paper:
          correction = sqrt(pi/(2*m)) * ||residual|| * dot(R @ query, signs)
        See https://arxiv.org/html/2406.03482, Definition 3.1, Equation 4;
        https://arxiv.org/html/2504.19874, Algorithm 2 stores ||residual||.
        Sign-only legacy payloads cannot recover magnitude; skip correction
        with a warning until their embeddings are recompressed.

        Args:
            query:     Query embedding.
            qjl_bits:  Packed sign bits from encode_residual().

        Returns:
            Estimated inner product correction (float).
        """
        self._ensure_projection(len(query))
        m = self._config.projection_dim
        norm, packed = _unpack_payload(qjl_bits, m)

        # Unpack bits
        bits = np.unpackbits(
            np.frombuffer(packed, dtype=np.uint8),
        )[:m]

        # Convert 0/1 to -1/+1
        signs = 2.0 * bits.astype(np.float64) - 1.0

        # Project query
        q_proj = self._R @ query

        # Asymmetric estimator
        correction = math.sqrt(math.pi / (2 * m)) * norm * float(np.dot(q_proj, signs))
        return correction
