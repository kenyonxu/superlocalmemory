# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""EAP Scheduler -- Embedding-Aware Precision.

Couples Ebbinghaus retention scores to embedding bit-width:
  R > 0.8  -> 32 (float32, full precision)
  R > 0.5  ->  8 (int8, sqlite-vec native)
  R > 0.2  ->  4 (polar 4-bit)
  R > 0.05 ->  2 (polar 2-bit)
  R <= 0.05->  0 (forgotten: marked, hidden from recall; vectors are KEPT)

The EAP cycle:
  1. Fetch all facts with retention data + current bit_width
  2. Map retention to target bit_width
  3. Execute downgrades (compress) and upgrades (restore)
  4. Return stats

HR-03: Original float32 is NEVER deleted by this scheduler. A forgotten memory
       is a logical state (``deleted`` in the stats counts those transitions);
       its vector and its stored embedding stay, so an upgrade or a re-access
       can restore it, and the vector backfill would rebuild a removed one.
       ``keep_float32_backup=False`` therefore changes nothing; a cycle says so
       instead of reporting a reclamation that did not happen.
HR-04: Quantization ONLY via EAP scheduler (not ad-hoc).

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from superlocalmemory.core.config import QuantizationConfig
from superlocalmemory.storage.embedding_codec import decode_embedding

if TYPE_CHECKING:
    from superlocalmemory.math.ebbinghaus import EbbinghausCurve
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.quantized_store import QuantizedEmbeddingStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retention -> bit-width mapping (Section 10, Pattern 4)
# ---------------------------------------------------------------------------


def retention_to_bit_width(retention: float) -> int:
    """Map Ebbinghaus retention to embedding precision.

    Returns:
        32, 8, 4, 2, or 0 (deleted).
    """
    if retention > 0.8:
        return 32
    if retention > 0.5:
        return 8
    if retention > 0.2:
        return 4
    if retention > 0.05:
        return 2
    return 0


# ---------------------------------------------------------------------------
# EAPScheduler
# ---------------------------------------------------------------------------


class EAPScheduler:
    """Embedding-Aware Precision scheduler.

    Runs periodic cycles that adjust embedding precision based on
    how well each fact is retained in memory.
    """

    # No __slots__: allows mock patching of _get_fact_embedding in tests

    def __init__(
        self,
        db: DatabaseManager,
        ebbinghaus: EbbinghausCurve,
        quantized_store: QuantizedEmbeddingStore,
        config: QuantizationConfig,
    ) -> None:
        """Initialize EAP scheduler.

        No side effects, no DB calls, no file I/O.
        """
        self._db = db
        self._ebbinghaus = ebbinghaus
        self._quantized_store = quantized_store
        self._config = config

    def run_eap_cycle(self, profile_id: str, *, dry_run: bool = False) -> dict:
        """Execute one EAP cycle for a profile.

        Steps:
          1. Fetch all facts with retention data
          2. Map retention -> target bit_width
          3. Execute downgrades/upgrades
          4. Return stats

        Returns:
            {total, downgrades, upgrades, skipped, deleted, errors}
        """
        stats = {
            "total": 0,
            "downgrades": 0,
            "upgrades": 0,
            "skipped": 0,
            "deleted": 0,
            "errors": 0,
            #: Vectors physically removed by this cycle. Always 0: see HR-03 above.
            "vectors_reclaimed": 0,
        }
        if not self._config.keep_float32_backup:
            stats["note"] = (
                "keep_float32_backup=False has no effect: forgotten memories are marked and "
                "hidden, and their vectors are kept (nothing is physically reclaimed)")
            logger.warning("EAP: %s", stats["note"])

        from superlocalmemory.storage.qjl_repair import repair_sketches

        try:
            stats.update(repair_sketches(self._db, self._quantized_store, profile_id,
                                        self._config, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 - report a failed repair query
            logger.error("QJL repair query failed: %s", exc)
            stats["repair_errors"] = 1

        self._run_precision_transitions(profile_id, stats, dry_run=dry_run)
        return stats

    def _run_precision_transitions(self, profile_id: str, stats: dict[str, Any],
                                   *, dry_run: bool = False) -> None:
        """Read EAP candidates and retain the existing transition policy."""
        try:
            rows = self._db.execute(
                "SELECT r.fact_id, r.retention_score, r.lifecycle_zone, "
                "  COALESCE(eqm.bit_width, 32) as current_bw "
                "FROM fact_retention r "
                "LEFT JOIN embedding_quantization_metadata eqm "
                "  ON r.fact_id = eqm.fact_id "
                "WHERE r.profile_id = ?",
                (profile_id,),
            )
        except Exception as exc:
            logger.error("EAP cycle query failed: %s", exc)
            stats["errors"] = 1
            return

        if not rows:
            return

        # Step 2+3: Process each fact
        for row in rows:
            self._transition_one(dict(row), profile_id, stats, dry_run=dry_run)

    def _transition_one(self, row: dict[str, Any], profile_id: str, stats: dict[str, Any],
                        *, dry_run: bool = False) -> None:
        fact_id, current_bw = row["fact_id"], int(row["current_bw"])
        target_bw = retention_to_bit_width(float(row["retention_score"]))
        stats["total"] += 1
        if target_bw == current_bw:
            stats["skipped"] += 1
        elif target_bw == 0:
            if not dry_run:
                self._handle_deletion(fact_id, profile_id)
            stats["deleted"] += 1
        else:
            downgrade = target_bw < current_bw
            counter = "downgrades" if downgrade else "upgrades"
            handler = self._handle_downgrade if downgrade else self._handle_upgrade
            if dry_run or handler(fact_id, profile_id, target_bw):
                stats[counter] += 1
            else:
                stats["errors" if downgrade else "skipped"] += 1

    # -- Handlers ----------------------------------------------------------

    def _handle_downgrade(
        self, fact_id: str, profile_id: str, target_bw: int,
    ) -> bool:
        """Compress a fact to lower bit_width.

        Fetches original float32 embedding, quantizes, and stores.
        """
        embedding = self._get_fact_embedding(fact_id)
        if embedding is None:
            logger.info(
                "No float32 embedding for %s, cannot compress", fact_id,
            )
            return False

        return self._quantized_store.compress_fact(
            fact_id, profile_id, embedding, target_bw,
        )

    def _handle_upgrade(
        self, fact_id: str, profile_id: str, target_bw: int,
    ) -> bool:
        """Restore a fact to higher precision.

        Only possible if original float32 is still in fact_embeddings.
        """
        # For upgrade to float32, just update metadata
        if target_bw == 32:
            try:
                self._db.execute(
                    "INSERT INTO embedding_quantization_metadata "
                    "(fact_id, profile_id, quantization_level, bit_width, created_at) "
                    "VALUES (?, ?, 'float32', 32, datetime('now')) "
                    "ON CONFLICT(fact_id) DO UPDATE SET "
                    "  quantization_level = 'float32', "
                    "  bit_width = 32",
                    (fact_id, profile_id),
                )
                return True
            except Exception as exc:
                logger.error("Upgrade to float32 failed for %s: %s", fact_id, exc)
                return False

        # For upgrade to int8 from polar, re-compress at higher precision
        embedding = self._get_fact_embedding(fact_id)
        if embedding is None:
            return False

        return self._quantized_store.compress_fact(
            fact_id, profile_id, embedding, target_bw,
        )

    def _handle_deletion(self, fact_id: str, profile_id: str) -> None:
        """Mark an embedding as forgotten. Logical only: no vector is removed (HR-03)."""
        try:
            self._db.execute(
                "INSERT INTO embedding_quantization_metadata "
                "(fact_id, profile_id, quantization_level, bit_width, created_at) "
                "VALUES (?, ?, 'deleted', 0, datetime('now')) "
                "ON CONFLICT(fact_id) DO UPDATE SET "
                "  quantization_level = 'deleted', "
                "  bit_width = 0",
                (fact_id, profile_id),
            )
        except Exception as exc:
            logger.error("Delete metadata failed for %s: %s", fact_id, exc)

    def _get_fact_embedding(self, fact_id: str) -> NDArray | None:
        """Retrieve original float32 embedding for a fact.

        Tries embedding_metadata -> fact_embeddings (vec0 table).
        Falls back to atomic_facts.embedding JSON column.
        """
        # Try atomic_facts.embedding (TEXT JSON or BLOB float32).
        # Two-stage error handling:
        #   - DB query failures (any exception from execute) → log + return None.
        #   - Format errors from decode_embedding (ValueError) → propagate.
        #     A corrupt embedding must NOT be silently collapsed to None because
        #     the caller cannot distinguish "embedding absent" from "data lost".
        raw = None
        try:
            rows = self._db.execute(
                "SELECT embedding FROM atomic_facts WHERE fact_id = ?",
                (fact_id,),
            )
            if rows:
                raw = dict(rows[0]).get("embedding")
        except Exception as exc:  # noqa: BLE001 — isolates DB failure from decode failure
            logger.debug(
                "Could not load embedding from atomic_facts for %s: %s",
                fact_id, exc,
            )
            return None

        if raw and raw != "null":
            # ValueError from decode_embedding propagates intentionally.
            vec = decode_embedding(raw, fact_id=fact_id)
            if vec:
                return np.array(vec, dtype=np.float64)

        return None
