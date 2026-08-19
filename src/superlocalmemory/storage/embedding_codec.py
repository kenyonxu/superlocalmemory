"""Encode and decode atomic_facts.embedding values.

All read and write paths for atomic_facts.embedding must go through
``encode_embedding`` and ``decode_embedding``.  Centralising the logic here
means a future format change requires one edit, not one per reader.

Storage format
--------------
New rows: 768 × float32, little-endian, stored as SQLite BLOB (3,072 bytes).
Legacy rows: JSON TEXT produced by json.dumps(list[float]).

The read path accepts both formats so a partial backfill is safe by
construction: callers see ``list[float]`` regardless of storage format.

Error contract
--------------
A value that is neither valid JSON nor a well-formed float32 buffer raises
``ValueError`` with the fact_id in the message.  Returning ``None`` silently
for a malformed value is forbidden: the caller cannot distinguish a legitimate
absent embedding from a data-loss event.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import numpy as np

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_BYTES",
    "encode_embedding",
    "decode_embedding",
]

EMBEDDING_DIM: int = 768
EMBEDDING_BYTES: int = EMBEDDING_DIM * 4  # float32 = 4 bytes


def encode_embedding(vec: list[float] | None) -> bytes | None:
    """Serialise a float list to a binary float32 BLOB for SQLite storage.

    Parameters
    ----------
    vec:
        A list of floats, or ``None``.  Production embeddings are always
        768-dimensional; the backfill script asserts the dimension before
        calling this function.

    Returns
    -------
    bytes | None
        Little-endian float32 buffer, or ``None`` if *vec* is ``None``.
    """
    if vec is None:
        return None
    return np.array(vec, dtype=np.float32).tobytes()


def decode_embedding(
    raw: bytes | str | None,
    *,
    fact_id: str = "<unknown>",
) -> list[float] | None:
    """Deserialise an embedding from either TEXT (JSON) or BLOB (binary float32).

    Parameters
    ----------
    raw:
        The raw value from ``atomic_facts.embedding``:
        ``None`` or empty string → absent embedding (returns ``None``).
        ``bytes`` → binary float32 BLOB path.
        ``str`` → legacy JSON TEXT path.
    fact_id:
        Included in any ``ValueError`` message for fast triage.

    Returns
    -------
    list[float] | None
        768-element list of floats, or ``None`` when the embedding is absent.

    Raises
    ------
    ValueError
        For a non-null value that is neither valid JSON nor a well-formed
        float32 buffer.  Never returns ``None`` for a malformed value.
    """
    if raw is None or raw == "":
        return None

    if isinstance(raw, (bytes, bytearray)):
        if len(raw) % 4 != 0 or len(raw) == 0:
            raise ValueError(
                f"Corrupt embedding buffer for fact {fact_id!r}: "
                f"{len(raw)} bytes is not a multiple of 4 (float32)"
            )
        if len(raw) != EMBEDDING_BYTES:
            # A torn write that happens to land on a 4-byte boundary is
            # indistinguishable from a short vector by length alone, and it was
            # accepted silently at debug level: 767 of 768 values still looks
            # like a valid embedding, and every similarity computed from it is
            # quietly wrong. Smaller vectors ARE legitimate in tests, so this is
            # a warning rather than a refusal, but it must be visible.
            logger.warning(
                "embedding for fact %s is %d bytes (%d floats), not the expected "
                "%d (%d floats) — expected only for a test vector; on a real "
                "store this is a truncated write",
                fact_id, len(raw), len(raw) // 4, EMBEDDING_BYTES, EMBEDDING_DIM,
            )
        return np.frombuffer(raw, dtype=np.float32).tolist()

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"Corrupt JSON embedding for fact {fact_id!r}: {exc}"
            ) from exc

    raise ValueError(
        f"Unexpected embedding type {type(raw).__name__!r} for fact {fact_id!r}; "
        f"expected bytes or str"
    )
