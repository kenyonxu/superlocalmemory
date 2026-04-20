# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory v3.4.21 — LLD-13 Track C.1

"""Inline trigram entity detection — hot-path Layer A of the two-layer
entity detector defined in ``LLD-13-inline-entity-detection.md``.

Design contract (do NOT improvise):

  - Hot-path ``lookup(text)`` targets **p99 < 2 ms**. Implemented as a
    single parameterised SQLite ``SELECT`` over a pre-built ``entity_trigrams``
    table in ``active_brain_cache.db`` plus a per-session ``@lru_cache``
    (≤200 entries, ≤100 KB total).
  - ``bootstrap()`` builds (or rebuilds) the cache table from
    ``canonical_entities`` + ``entity_aliases`` in ``memory.db``. It
    runs under ``core.ram_lock.ram_reservation('trigram_rebuild',
    required_mb=300)`` per LLD-00 §7.
  - ``memory.db`` is **SACRED** — this module only READS from
    ``canonical_entities`` / ``entity_aliases``. Never writes.
  - ``cache.db`` is **NOT a migration target** (LLD-00 §6). The index
    table is (re)created via ``CREATE TABLE IF NOT EXISTS`` inside
    ``bootstrap()``. ``slm cache clear`` and first-run both hit this
    lazy path.
  - Every SQL call uses parameterised queries (SEC-C-03). The IN-clause
    placeholder count is bounded (≤256 trigrams).
  - SQLite connections open with ``busy_timeout=50`` so a locked DB
    fails fast rather than eating the hook budget.

Stdlib-only imports at module load. The singleton helper
``get_or_none()`` returns a shared ``TrigramIndex`` instance or ``None``
if the cache DB is absent; the hook uses this to fall back silently.
"""

from __future__ import annotations

import sqlite3
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Import the RAM semaphore at module scope so tests can monkeypatch
# ``trigram_index.ram_reservation`` to a no-op on CI boxes with tight RAM.
from superlocalmemory.core.ram_lock import ram_reservation


# --------------------------------------------------------------------------
# Module constants
# --------------------------------------------------------------------------

_ACTIVE_PROFILE: str = "default"
_BUSY_TIMEOUT_MS: int = 50
_MAX_IN_CLAUSE: int = 256
_MAX_INPUT_CHARS: int = 500


# H-12/H-P-06: module-level cached connection for the inline lookup
# path. The first cache miss on a fresh session previously paid the
# ``sqlite3.connect`` cost (~1–3 ms warm, blowing the <2 ms p99
# budget). With a shared conn (guarded by ``_CACHE_CONN_LOCK``), every
# lookup pays only the query cost. ``_reset_cache_conn()`` exists so
# tests + ``bootstrap()`` can drop a stale conn after the cache DB is
# rebuilt.
_CACHE_CONN: Optional[sqlite3.Connection] = None
_CACHE_CONN_LOCK = threading.Lock()


def _get_cache_conn() -> Optional[sqlite3.Connection]:
    """Return a process-cached connection to the trigram cache DB.

    Returns ``None`` if the cache DB is missing or the connect fails.
    Caller holds no lock — every ``execute`` is serialised via
    ``_CACHE_CONN_LOCK``.
    """
    global _CACHE_CONN
    if _CACHE_CONN is not None:
        return _CACHE_CONN
    with _CACHE_CONN_LOCK:
        if _CACHE_CONN is not None:
            return _CACHE_CONN
        if not TrigramIndex.CACHE_DB_PATH.exists():
            return None
        try:
            conn = sqlite3.connect(
                str(TrigramIndex.CACHE_DB_PATH),
                timeout=0.05,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        except sqlite3.OperationalError:
            return None
        _CACHE_CONN = conn
        return _CACHE_CONN


def _reset_cache_conn() -> None:
    """Drop the cached connection. Called after ``bootstrap()`` swaps
    the cache table so subsequent lookups re-connect to the fresh DB.
    """
    global _CACHE_CONN
    with _CACHE_CONN_LOCK:
        if _CACHE_CONN is not None:
            try:
                _CACHE_CONN.close()
            except sqlite3.Error:  # pragma: no cover — defensive
                pass
            _CACHE_CONN = None


# --------------------------------------------------------------------------
# Trigram extraction (stdlib-only, deterministic, NFKD + ASCII-fold)
# --------------------------------------------------------------------------


def _trigrams_for(text: str) -> set[str]:
    """Extract 3-gram set from ``text``.

    Pipeline: clamp-to-500-chars -> NFKD normalize -> ASCII-fold ->
    lowercase -> split on non-alphanumeric -> skip tokens < 3 chars ->
    emit overlapping 3-grams per token.

    Matches LLD-13 §4.1 exactly. stdlib-only.
    """
    if not text:
        return set()
    s = unicodedata.normalize("NFKD", text[:_MAX_INPUT_CHARS])
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = "".join(c if c.isalnum() else " " for c in s)
    out: set[str] = set()
    for token in s.split():
        if len(token) < 3:
            continue
        for i in range(len(token) - 2):
            out.add(token[i : i + 3])
    return out


# --------------------------------------------------------------------------
# TrigramIndex
# --------------------------------------------------------------------------


class TrigramIndex:
    """Two-layer entity detection — Layer A (hot inline).

    Bootstrap reads ``canonical_entities`` + ``entity_aliases`` from
    the SLM source-of-truth DB and writes a compact
    ``(trigram, entity_id, weight)`` table into the cache DB. The hot
    path does one grouped SELECT per prompt and returns up to 10 ranked
    ``(entity_id, hits)`` candidates.
    """

    CACHE_DB_PATH: Path = Path.home() / ".superlocalmemory" / "active_brain_cache.db"
    MAX_TRIGRAMS: int = 1_000_000
    LOOKUP_LIMIT: int = 10
    LOOKUP_MIN_HITS: int = 2

    # ----------------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------------

    def __init__(self, source_db_path: Path) -> None:
        if not isinstance(source_db_path, Path):
            raise ValueError("source_db_path must be a pathlib.Path")
        self._source_db_path = source_db_path
        # Per-instance LRU wrapper (200 entries, ≤100 KB envelope).
        self._cached_lookup_key = lru_cache(maxsize=200)(self._lookup_raw)

    # ----------------------------------------------------------------------
    # bootstrap() — daemon-side rebuild
    # ----------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Read canonical_entities + entity_aliases, recompute trigram
        buckets, atomically swap the cache table.

        Wraps the heavy phase in ``ram_reservation('trigram_rebuild',
        required_mb=300)``. Source DB is opened read-only; memory.db is
        never mutated.
        """
        with ram_reservation("trigram_rebuild", required_mb=300):
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        buckets: dict[str, dict[str, float]] = {}
        src = sqlite3.connect(
            f"file:{self._source_db_path}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        try:
            # One LEFT JOIN fetch. Params: active profile.
            rows = src.execute(
                "SELECT ce.entity_id, ce.canonical_name, "
                "       COALESCE(ea.alias, '') AS alias "
                "FROM canonical_entities ce "
                "LEFT JOIN entity_aliases ea USING (entity_id) "
                "WHERE ce.profile_id = ?",
                (_ACTIVE_PROFILE,),
            ).fetchall()
        finally:
            src.close()

        for entity_id, canonical_name, alias in rows:
            for name in (canonical_name, alias):
                if not name:
                    continue
                for tri in _trigrams_for(str(name)):
                    buckets.setdefault(tri, {}).setdefault(entity_id, 0.0)
                    buckets[tri][entity_id] += 1.0

        flat: list[tuple[str, str, float]] = [
            (tri, eid, w)
            for tri, d in buckets.items()
            for eid, w in d.items()
        ]
        # Cap total rows by highest weight first.
        if len(flat) > self.MAX_TRIGRAMS:
            flat.sort(key=lambda r: r[2], reverse=True)
            flat = flat[: self.MAX_TRIGRAMS]

        # Write to cache DB via atomic shadow-table swap.
        self.CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.CACHE_DB_PATH), timeout=2.0)
        try:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entity_trigrams (
                    trigram   TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    weight    REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (trigram, entity_id)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_trigram_lookup
                    ON entity_trigrams (trigram);
                CREATE TABLE IF NOT EXISTS entity_trigrams_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                DROP TABLE IF EXISTS entity_trigrams_shadow;
                CREATE TABLE entity_trigrams_shadow (
                    trigram   TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    weight    REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (trigram, entity_id)
                ) WITHOUT ROWID;
                """
            )
            with conn:
                conn.executemany(
                    "INSERT INTO entity_trigrams_shadow (trigram, entity_id, weight) "
                    "VALUES (?, ?, ?)",
                    flat,
                )
                conn.execute("DROP TABLE entity_trigrams")
                conn.execute(
                    "ALTER TABLE entity_trigrams_shadow "
                    "RENAME TO entity_trigrams"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trigram_lookup "
                    "ON entity_trigrams (trigram)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO entity_trigrams_meta (key, value) "
                    "VALUES (?, ?)",
                    ("entity_count", str(len(flat))),
                )
        finally:
            conn.close()

        # Bust the per-instance LRU — stale entries would point at now-
        # dropped rows.
        self._cached_lookup_key.cache_clear()
        # H-12/H-P-06: also drop the module-level cached conn so the
        # next lookup re-connects to the post-swap schema. Without this
        # a long-lived process could hold a handle pinned to the
        # (dropped-and-recreated) table.
        _reset_cache_conn()

    # ----------------------------------------------------------------------
    # lookup() — hot path
    # ----------------------------------------------------------------------

    def lookup(self, text: str) -> list[tuple[str, int]]:
        """Return up to ``LOOKUP_LIMIT`` ``(entity_id, hits)`` matches,
        ordered by hit count DESC, weight DESC.

        Returns ``[]`` on any failure (missing table, locked DB, empty
        trigram set). Target p99 < 2 ms.
        """
        if not text:
            return []
        trigrams = _trigrams_for(text)
        if not trigrams:
            return []
        if len(trigrams) > _MAX_IN_CLAUSE:
            # Deterministic truncation — sorted for stability so the LRU
            # keys stay hashable and repeatable across identical prompts.
            trigrams = set(sorted(trigrams)[:_MAX_IN_CLAUSE])
        key = frozenset(trigrams)
        try:
            return list(self._cached_lookup_key(key))
        except Exception:
            # Any failure: self-heal cache + fall back to a direct query.
            try:
                self._cached_lookup_key.cache_clear()
            except Exception:
                pass
            try:
                return list(self._lookup_raw(key))
            except Exception:
                return []

    def _lookup_raw(self, trigrams: frozenset[str]) -> tuple[tuple[str, int], ...]:
        """SQLite-backed lookup. Returns a tuple (hashable for LRU)."""
        if not trigrams:
            return ()
        if not self.CACHE_DB_PATH.exists():
            return ()

        params = tuple(trigrams)
        placeholders = ",".join("?" * len(params))
        sql = (
            "SELECT entity_id, COUNT(*) AS hits, SUM(weight) AS score "
            "FROM entity_trigrams "
            f"WHERE trigram IN ({placeholders}) "
            "GROUP BY entity_id "
            "HAVING hits >= ? "
            "ORDER BY hits DESC, score DESC "
            "LIMIT ?"
        )
        bound = params + (self.LOOKUP_MIN_HITS, self.LOOKUP_LIMIT)

        # H-12/H-P-06: use the module-cached connection; fall back to a
        # fresh connect only when the cache is empty (first-lookup-in-
        # process or post-rebuild). ``_CACHE_CONN_LOCK`` serialises
        # access because ``check_same_thread=False`` lets worker threads
        # share the conn with the hot path.
        conn = _get_cache_conn()
        if conn is not None:
            try:
                with _CACHE_CONN_LOCK:
                    rows = conn.execute(sql, bound).fetchall()
            except sqlite3.OperationalError:
                # Table missing/locked or conn stale — drop + one-shot
                # fresh connect as the defensive fallback path.
                _reset_cache_conn()
                conn = None
        if conn is None:
            try:
                fresh = sqlite3.connect(
                    str(self.CACHE_DB_PATH),
                    timeout=0.05,  # 50 ms connection timeout
                    isolation_level=None,
                )
            except sqlite3.OperationalError:
                return ()
            try:
                fresh.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
                try:
                    rows = fresh.execute(sql, bound).fetchall()
                except sqlite3.OperationalError:
                    return ()
            finally:
                fresh.close()
        return tuple((eid, int(hits)) for (eid, hits, _score) in rows)


# --------------------------------------------------------------------------
# Singleton accessor used by the hook
# --------------------------------------------------------------------------


_SINGLETON: Optional[TrigramIndex] = None


def get_or_none() -> Optional[TrigramIndex]:
    """Return a process-local ``TrigramIndex`` if the cache DB exists,
    else ``None`` so the hook can fall back to the regex-only signature.

    Test fixtures monkeypatch this module-level function directly.
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    if not TrigramIndex.CACHE_DB_PATH.exists():
        return None
    default_source = Path.home() / ".superlocalmemory" / "memory.db"
    if not default_source.exists():
        return None
    _SINGLETON = TrigramIndex(source_db_path=default_source)
    return _SINGLETON


__all__ = ("TrigramIndex", "get_or_none")
