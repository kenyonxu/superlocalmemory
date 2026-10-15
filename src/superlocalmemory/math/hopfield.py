# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""Modern Continuous Hopfield Network (Ramsauer et al., 2020).

Implementation of the continuous Hopfield energy and update rules
from "Hopfield Networks is All You Need" (arXiv 2008.02217).

This is the mathematical foundation for the 6th retrieval channel.
The Hopfield update is equivalent to single-head self-attention:
  xi_new = X' @ softmax(beta * X @ xi)

Key properties:
  - Energy function: E(xi) = -logsumexp(beta * X @ xi) + beta/2 * ||xi||^2
  - Update rule: xi_new = X' @ softmax(beta * X @ xi)
  - Beta: sqrt(d) for unit-norm patterns (configurable inverse temperature)
  - Storage capacity: O(exp(d/2)) -- exponential in dimension
  - Convergence: 1 step for well-separated patterns

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from superlocalmemory.core.hopfield_config import HopfieldConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hopfield State (immutable result)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HopfieldState:
    """Result of a single Hopfield retrieval step."""

    retrieved_pattern: np.ndarray    # d-dimensional completed pattern
    attention_weights: np.ndarray    # n-dimensional softmax weights
    energy_before: float             # E(xi) before update
    energy_after: float              # E(xi_new) after update
    converged: bool                  # energy_after <= energy_before
    iterations: int                  # Number of update steps taken


# ---------------------------------------------------------------------------
# Modern Continuous Hopfield Network
# ---------------------------------------------------------------------------

class ModernHopfieldNetwork:
    """Modern Continuous Hopfield Network (Ramsauer et al., 2020).

    Provides energy computation, single-step update, full retrieval
    with convergence detection, and attention scoring.

    Usage::

        net = ModernHopfieldNetwork(HopfieldConfig(dimension=768))
        state = net.retrieve(query_vec, memory_matrix)
        scores = net.attention_scores(query_vec, memory_matrix)
    """

    def __init__(self, config: HopfieldConfig) -> None:
        """Choose attention scaling for the unit-norm pattern contract."""
        if config.dimension <= 0:
            raise ValueError("dimension must be positive")
        self._config = config
        # Ramsauer et al., arXiv:2008.02217, attention/update equations:
        # beta is a free inverse temperature, not a mandated 1/sqrt(d).
        # Author implementation permits explicit scaling (checked 2026-10-11):
        # https://github.com/ml-jku/hopfield-layers/blob/master/hflayers/functional.py
        # Scaling sqrt(d)-norm Q/K by 1/sqrt(d) is equivalent to scaling
        # our unit-norm Q/K by sqrt(d). Tiny logits instead average the corpus.
        beta = getattr(config, "beta", None)
        self._beta = math.sqrt(config.dimension) if beta is None else float(beta)
        if not math.isfinite(self._beta) or self._beta <= 0:
            raise ValueError("beta must be finite and positive")

    # -- Public API ---------------------------------------------------------

    def energy(self, xi: np.ndarray, memory_matrix: np.ndarray) -> float:
        """Compute Modern Hopfield energy function.

        E(xi) = -logsumexp(beta * X @ xi) + beta/2 * ||xi||^2

        HR-04: Uses numerically stable logsumexp (shift by max).

        Args:
            xi: Query vector, shape (d,).
            memory_matrix: Stored patterns, shape (n, d). L2-normalized rows.

        Returns:
            Energy value (float). Lower = better match to stored patterns.
            Returns 0.0 for empty memory matrix.
        """
        # Guard: empty matrix
        if memory_matrix.shape[0] == 0:
            return 0.0

        xi_64 = xi.astype(np.float64)  # HR-11: float64 for energy
        mem_64 = memory_matrix.astype(np.float64)

        # Compute logits: beta * X @ xi, shape (n,)
        logits = self._beta * (mem_64 @ xi_64)

        # HR-04: Numerically stable logsumexp
        max_logit = float(np.max(logits))
        lse = max_logit + float(np.log(np.sum(np.exp(logits - max_logit))))

        # Energy: -lse + beta/2 * ||xi||^2
        energy = -lse + (self._beta / 2.0) * float(np.dot(xi_64, xi_64))

        # Guard against NaN (degenerate all-zero matrix)
        if math.isnan(energy):
            return 0.0

        return energy

    def update(self, xi: np.ndarray, memory_matrix: np.ndarray) -> np.ndarray:
        """Single Hopfield update step.

        xi_new = X' @ softmax(beta * X @ xi)

        HR-02: Uses numerically stable softmax (shift by max).

        Args:
            xi: Query vector, shape (d,).
            memory_matrix: Stored patterns, shape (n, d). L2-normalized rows.

        Returns:
            Updated vector xi_new, shape (d,). Returns zeros if matrix is empty.
        """
        d = self._config.dimension

        # Guard: empty matrix
        if memory_matrix.shape[0] == 0:
            return np.zeros(d, dtype=np.float32)

        # Compute logits: beta * X @ xi, shape (n,)
        logits = self._beta * (memory_matrix @ xi)

        # HR-02: Numerically stable softmax
        attention = self._softmax(logits)

        # Pattern completion: X' @ attention, shape (d,)
        xi_new = memory_matrix.T @ attention

        return xi_new.astype(np.float32)

    def retrieve(
        self,
        query: np.ndarray,
        memory_matrix: np.ndarray,
        max_iterations: int = 0,
    ) -> HopfieldState:
        """Full retrieval with convergence detection and energy tracking.

        Iteratively applies the Hopfield update rule until energy converges
        or max_iterations is reached.

        Args:
            query: Query vector, shape (d,).
            memory_matrix: Stored patterns, shape (n, d).
            max_iterations: Override for config.max_iterations. 0 = use config.

        Returns:
            HopfieldState with retrieved pattern, attention, energy, convergence info.
        """
        d = self._config.dimension

        # Guard: empty matrix
        if memory_matrix.shape[0] == 0:
            return HopfieldState(
                retrieved_pattern=np.zeros(d, dtype=np.float32),
                attention_weights=np.array([], dtype=np.float32),
                energy_before=0.0,
                energy_after=0.0,
                converged=False,
                iterations=0,
            )

        iters = max_iterations if max_iterations > 0 else self._config.max_iterations

        # HR-11: float64 for energy computation precision
        xi = query.copy().astype(np.float64)
        mem_f32 = memory_matrix.astype(np.float32)  # HR-11: float32 for memory

        energy_initial = self.energy(xi.astype(np.float32), mem_f32)
        e_before = energy_initial
        xi_new = xi.copy()

        iteration = 0
        for iteration in range(iters):
            xi_new = self.update(xi.astype(np.float32), mem_f32).astype(np.float64)
            e_after = self.energy(xi_new.astype(np.float32), mem_f32)

            # Convergence check
            if abs(e_after - e_before) < self._config.convergence_epsilon:
                break

            xi = xi_new
            e_before = e_after

        # Final energy
        e_final = self.energy(xi_new.astype(np.float32), mem_f32)

        # Final attention weights
        attention = self._softmax(
            self._beta * (mem_f32 @ xi_new.astype(np.float32)),
        )

        return HopfieldState(
            retrieved_pattern=xi_new.astype(np.float32),
            attention_weights=attention.astype(np.float32),
            energy_before=energy_initial,
            energy_after=e_final,
            converged=(e_final <= energy_initial + 1e-9),
            iterations=iteration + 1,
        )

    def attention_scores(
        self,
        query: np.ndarray,
        memory_matrix: np.ndarray,
    ) -> np.ndarray:
        """Compute Hopfield attention weights WITHOUT full update.

        Used for scoring/ranking without pattern completion.

        Args:
            query: Query vector, shape (d,).
            memory_matrix: Stored patterns, shape (n, d).

        Returns:
            Attention weights, shape (n,), summing to 1.0.
        """
        if memory_matrix.shape[0] == 0:
            return np.array([], dtype=np.float32)

        logits = self._beta * (memory_matrix @ query)
        return self._softmax(logits)

    # -- Private helpers ----------------------------------------------------

    def _softmax(self, logits: np.ndarray) -> np.ndarray:
        """Numerically stable softmax.

        HR-02: Shift by max to prevent overflow.
        softmax(x) = exp(x - max(x)) / sum(exp(x - max(x)))
        """
        shifted = logits - np.max(logits)  # HR-02
        exp_vals = np.exp(shifted)
        return exp_vals / np.sum(exp_vals)
