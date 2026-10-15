"""Read-only upgrade diagnostics and restartable repair of derived QJL sketches.

Canonical vectors and base quantized bytes are never overwritten. Each sketch
and its size metadata commit together; interrupted rows roll back and a rerun
selects only sketches that still need repair.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import numpy as np

from superlocalmemory.math.qjl import _HEADER, _VERSION, _unpack_payload
from superlocalmemory.storage.embedding_codec import decode_embedding

if TYPE_CHECKING:
    from superlocalmemory.core.config import QuantizationConfig
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.quantized_store import QuantizedEmbeddingStore

logger = logging.getLogger(__name__)


def _reason(payload: bytes | None, projection_dim: int) -> str:
    if payload is None:
        return "legacy"
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return "incompatible"
    try:
        payload = bytes(payload)
    except (ValueError, TypeError):
        return "incompatible"
    if not payload:
        return "legacy"
    sign_size = (projection_dim + 7) // 8
    if payload.startswith(_VERSION):
        if len(payload) != _HEADER.size + sign_size:
            return "incompatible"
    elif len(payload) == sign_size:
        return "legacy"
    try:
        _unpack_payload(payload, projection_dim)
    except (ValueError, TypeError):
        return "incompatible"
    return ""


def repair_rows(db: DatabaseManager | sqlite3.Connection, profile_id: str,
                config: QuantizationConfig) -> list[dict[str, Any]]:
    """List affected rows, also for stores without retained original vectors."""
    rows = db.execute(
        "SELECT fact_id, qjl_bits, bit_width FROM polar_embeddings "
        "WHERE profile_id=? AND bit_width IN (2,4)", (profile_id,))
    return [dict(row) | {"reason": reason} for row in rows
            if (reason := _reason(row["qjl_bits"], config.qjl.projection_dim))]


def original_vector(db: DatabaseManager | sqlite3.Connection, fact_id: str,
                    profile_id: str, dimension: int) -> np.ndarray | None:
    snapshot = _original_snapshot(db, fact_id, profile_id, dimension)
    return snapshot[1] if snapshot is not None else None


def _original_snapshot(db: DatabaseManager | sqlite3.Connection, fact_id: str,
                       profile_id: str, dimension: int) -> tuple[bytes | str, np.ndarray] | None:
    """Keep the exact stored source alongside the vector decoded from it."""
    try:
        rows = list(db.execute(
            "SELECT embedding FROM atomic_facts WHERE fact_id=? AND profile_id=?",
            (fact_id, profile_id)))
        source = rows[0]["embedding"] if rows else None
        vector = decode_embedding(source, fact_id=fact_id)
        if vector is None:
            return None
        vector = np.asarray(vector, dtype=np.float64)
        if vector.shape != (dimension,) or not np.isfinite(vector).all():
            return None
        return source, vector
    except (ValueError, TypeError):
        return None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise


def repair_status(db: DatabaseManager | sqlite3.Connection, profile_id: str,
                  config: QuantizationConfig) -> dict[str, int]:
    rows = repair_rows(db, profile_id, config)
    return {"repair_needed": len(rows),
            "legacy": sum(row["reason"] == "legacy" for row in rows),
            "incompatible": sum(row["reason"] == "incompatible" for row in rows),
            "unrecoverable": sum(original_vector(db, row["fact_id"], profile_id,
                                                 config.polar.dimension) is None for row in rows)}


def _repair_one(db: DatabaseManager, store: QuantizedEmbeddingStore,
                row: dict[str, Any], profile_id: str, vector: np.ndarray,
                source: bytes | str) -> None:
    qe = store.load(row["fact_id"], profile_id, include_qjl=False)
    if qe is None:
        raise ValueError("base embedding unavailable")
    base = store._polar.decode(qe)
    if base.shape != vector.shape or not np.isfinite(base).all():
        raise ValueError("invalid base embedding geometry")
    payload = store._qjl.encode_residual(vector - base)
    with db.transaction():
        # Validate both reads in one write statement: the source and base used
        # to encode the residual must coexist unchanged at this linearization
        # point. SQLite holds the writer lock through the metadata commit.
        updated = db.execute(
            "UPDATE polar_embeddings SET qjl_bits=? WHERE fact_id=? AND profile_id=? "
            "AND radius=? AND angle_indices=? AND qjl_bits IS ? AND bit_width=? "
            "AND EXISTS (SELECT 1 FROM atomic_facts WHERE fact_id=? AND profile_id=? "
            "AND embedding IS ?) RETURNING fact_id",
            (payload, row["fact_id"], profile_id, qe.radius, qe.angle_indices,
             row["qjl_bits"], qe.bit_width, row["fact_id"], profile_id, source))
        if not updated:
            raise ValueError("canonical or quantized embedding changed concurrently; rerun repair")
        db.execute("UPDATE embedding_quantization_metadata SET compressed_size_bytes=? "
                   "WHERE fact_id=? AND profile_id=?",
                   (len(qe.angle_indices) + len(payload), row["fact_id"], profile_id))


def repair_sketches(db: DatabaseManager, store: QuantizedEmbeddingStore, profile_id: str,
                    config: QuantizationConfig, *, dry_run: bool = False) -> dict[str, int]:
    stats = {"repair_needed": 0, "repaired": 0, "unrecoverable": 0, "repair_errors": 0}
    if store._qjl is None:
        return stats
    for row in repair_rows(db, profile_id, config):
        stats["repair_needed"] += 1
        snapshot = _original_snapshot(db, row["fact_id"], profile_id, store._polar._d)
        if snapshot is None:
            stats["unrecoverable"] += 1
            logger.warning("QJL fact_id=%s needs re-embedding; no usable original vector",
                           row["fact_id"])
            continue
        if dry_run:
            continue
        try:
            source, vector = snapshot
            _repair_one(db, store, row, profile_id, vector, source)
            stats["repaired"] += 1
            logger.info("QJL repaired fact_id=%s", row["fact_id"])
        except (ValueError, TypeError, IndexError, KeyError, sqlite3.Error) as exc:
            stats["repair_errors"] += 1
            logger.warning("QJL repair failed fact_id=%s: %s", row["fact_id"], exc)
    return stats
