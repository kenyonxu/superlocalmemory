# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Canonical unit-pattern Hopfield settings, shared by config and mathematics."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger("superlocalmemory.core.config")


@dataclass(frozen=True)
class HopfieldConfig:
    """Modern Continuous Hopfield Network configuration (Ramsauer et al., 2020).

    Energy: E(xi) = -log(sum_i exp(B * xi' * x_i)) + B/2 * ||xi||^2
    Update: xi_new = X' @ softmax(B * X @ xi)
    Beta:   B = sqrt(d) for unit-norm patterns, or an explicit beta
    Storage capacity: O(e^{d/2}) -- exponential in dimension.
    """

    enabled: bool = True
    dimension: int = 768
    max_iterations: int = 1
    convergence_epsilon: float = 1e-6
    prefilter_threshold: int = 10_000
    # How many candidates the index hands to this stage. This stage decides final
    # membership, so a fact ranked past this number by the index never reaches it
    # and can never be returned — the size of this pool is a direct limit on which
    # memories are reachable at all.
    #
    #
    # Chosen by measurement against the 2 s recall ceiling, on a 517 MB store:
    #   150 -> p95   998 ms, 0 channel timeouts
    #   300 -> p95 1054 ms, 0
    #   500 -> p95 1140 ms, 0      <- here: 3.3x the reachability of 150 for +142 ms
    #   750 -> p95 1278 ms, 0
    #  1000 -> p95 1947-2737 ms, 2-25 timeouts
    #
    # 1000 is excluded twice over: it breaches the ceiling, and a channel that
    # misses its deadline contributes nothing to fusion — so whether it lands
    # varies with machine load and the same question stops returning the same
    # answer. 500 keeps ~860 ms of headroom, which is several times the observed
    # run-to-run variance, and stays clear of the region where the cost curve
    # turned non-linear for reasons that were never explained.
    prefilter_candidates: int = 500
    skip_threshold: int = 100_000
    cache_ttl_seconds: float = 60.0
    beta: float | None = None

    def __post_init__(self) -> None:
        if self.beta is None:
            return
        if (isinstance(self.beta, bool) or not isinstance(self.beta, (int, float))
                or not math.isfinite(self.beta) or self.beta <= 0):
            raise ValueError("beta must be finite and positive")


def load_hopfield_config(section: object) -> HopfieldConfig:
    """Load a typed section; invalid settings warn and fall back to defaults."""
    if section is None:
        return HopfieldConfig()
    try:
        if not isinstance(section, dict):
            raise ValueError("hopfield section must be an object")
        return HopfieldConfig(**{
            key: value for key, value in section.items()
            if key in HopfieldConfig.__dataclass_fields__
        })
    except (TypeError, ValueError, OverflowError):
        logger.warning("Ignoring invalid hopfield config — using defaults")
        return HopfieldConfig()
