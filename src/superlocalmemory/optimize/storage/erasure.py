# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""Erasing a person from ``llmcache.db``: the MCP cache and the reversible originals.

``slm_cache_set`` stores whatever text an agent routes through it, and
``slm_compress`` keeps the exact original of a lossy compression so
``slm_retrieve`` can return it. Both are encrypted, but with a key that stays on
this computer, so "encrypted" does not mean "unreadable": until now an erased
profile's recalled text could still be read back through both tools.

Which rows are a person's is answered by the profile each row recorded when it
was written (``profile_id``). A row from before that column existed has ``''``:
its owner is unknown. This is a cache, and a copy that might be someone's must
not be kept to spare a cache miss, so a whole-profile erasure removes those too
and says how many. A targeted erasure (one fact, one entity) knows the words it
removed, so it removes the rows of that profile, and the unowned ones, that
contain them.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

_CHUNK = 500


@dataclass
class CacheErasure:
    """What an erasure removed from the Optimize cache, and whether it can be trusted."""

    entries: int = 0
    originals: int = 0
    #: Rows with no recorded owner that were removed because they might be this person's.
    unattributed: int = 0
    failed: bool = False
    detail: list[str] = field(default_factory=list)

    def as_counts(self) -> dict[str, int]:
        out = {"optimize_cache_entries": self.entries,
               "optimize_cache_originals": self.originals}
        if self.unattributed:
            out["optimize_cache_unattributed"] = self.unattributed
        if self.failed:
            out["optimize_cache_failed"] = 1
        return out


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _plain(cache: Any, blob: bytes) -> bytes:
    return zlib.decompress(cache._decrypt(blob))


def _holds(cache: Any, blob: bytes, needles: Sequence[bytes]) -> bool:
    """Whether the stored text contains any of ``needles``. Unreadable counts as yes."""
    try:
        text = _plain(cache, blob)
    except Exception:  # noqa: BLE001 - a row that cannot be read cannot be shown clean
        return True
    return any(n in text for n in needles)


def erase_profile(cache: Any, profile_id: str, *,
                  needles: Iterable[str] | None = None) -> CacheErasure:
    """Remove the profile's rows from the cache; with ``needles``, only rows holding them.

    ``cache`` is a :class:`CacheDB`. Never raises: a failure is returned, so the
    erasure that called this can report itself incomplete.
    """
    result = CacheErasure()
    wanted = None if needles is None else [n.encode("utf-8") for n in needles if n]
    if wanted is not None and not wanted:
        return result
    try:
        db = cache._db
        with db.transaction():
            _erase_entries(cache, db, profile_id, wanted, result)
            _erase_originals(cache, db, profile_id, wanted, result)
            _drop_empty_centroids(db)
        _scrub(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Optimize cache erase failed: %s", exc)
        result.failed = True
        result.detail.append(type(exc).__name__)
    return result


def _rows(db: Any, sql: str, params: tuple) -> list[dict]:
    return [dict(r) for r in db.execute(sql, params)]


def _erase_entries(cache: Any, db: Any, profile_id: str, wanted: list[bytes] | None,
                   result: CacheErasure) -> None:
    rows = _rows(db, "SELECT entry_id, cache_key, tenant_id, profile_id, value_blob "
                     "FROM llmcache_entries WHERE profile_id = ? OR profile_id = ''",
                 (profile_id,))
    chosen = [r for r in rows if wanted is None or _holds(cache, r["value_blob"], wanted)]
    for start in range(0, len(chosen), _CHUNK):
        part = chosen[start:start + _CHUNK]
        ids = [r["entry_id"] for r in part]
        marks = _marks(len(ids))
        for table in ("llmcache_semantic_vectors", "llmcache_boundaries"):
            db.execute(f"DELETE FROM {table} WHERE entry_id IN ({marks})", tuple(ids))
        for r in part:
            db.execute("DELETE FROM llmcache_tags WHERE cache_key = ? AND tenant_id = ?",
                       (r["cache_key"], r["tenant_id"]))
        db.execute(f"DELETE FROM llmcache_entries WHERE entry_id IN ({marks})", tuple(ids))
    result.entries = len(chosen)
    result.unattributed += sum(1 for r in chosen if r["profile_id"] == "")


def _erase_originals(cache: Any, db: Any, profile_id: str, wanted: list[bytes] | None,
                     result: CacheErasure) -> None:
    rows = _rows(db, "SELECT ccr_id, tenant_id, profile_id, original_blob "
                     "FROM llmcache_ccr_originals WHERE profile_id = ? OR profile_id = ''",
                 (profile_id,))
    chosen = [r for r in rows if wanted is None or _holds(cache, r["original_blob"], wanted)]
    for start in range(0, len(chosen), _CHUNK):
        ids = [r["ccr_id"] for r in chosen[start:start + _CHUNK]]
        db.execute(f"DELETE FROM llmcache_ccr_originals WHERE ccr_id IN ({_marks(len(ids))})",
                   tuple(ids))
    result.originals = len(chosen)
    result.unattributed += sum(1 for r in chosen if r["profile_id"] == "")


def _drop_empty_centroids(db: Any) -> None:
    """A tenant's centroid is an average of its cached prompts' embeddings; none left, none kept."""
    db.execute(
        "DELETE FROM llmcache_centroids WHERE tenant_id NOT IN "
        "(SELECT DISTINCT tenant_id FROM llmcache_entries)")


def _scrub(db: Any) -> None:
    """Deleted rows stay readable in the file's free pages until it is rewritten."""
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("VACUUM")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
