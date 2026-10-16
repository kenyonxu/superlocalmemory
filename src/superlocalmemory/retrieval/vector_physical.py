# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""What the vector index physically holds, read and removed without an engine.

Three callers need this and none of them has a ``VectorStore``:

* ``slm doctor`` claims "all of your memories can be found by asking a
  question". That is a claim about the vec0 index, so it has to read the index,
  not the bookkeeping beside it (``embedding_metadata`` survives a lost index).
* ``slm gdpr erase`` runs with a database handle and no engine. Without a store
  it deleted the bookkeeping rows and left every vector in the index, which
  carries a person's text in a form that can be inverted.
* An erasure that has deleted what it knows about wants to sweep the profile's
  partition for rows nothing points at.

The predicate everywhere is the one search uses: a vector counts only when its
vec0 row exists, carries the profile as its partition key, and has a metadata
row for the same fact and profile.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CHUNK = 500


class VectorIndexUnreadable(RuntimeError):
    """The vec0 index cannot be opened here (no extension support, or a damaged file)."""


@dataclass(frozen=True)
class VectorPurge:
    """What a sweep of one profile's vectors did."""

    removed: int = 0
    remaining: int = 0
    #: ``True`` when there was no vector table at all, so there was nothing to read.
    no_index: bool = False


def open_vec(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """A connection with sqlite-vec loaded; raises :class:`VectorIndexUnreadable`."""
    try:
        import sqlite_vec
    except ImportError as exc:
        raise VectorIndexUnreadable("sqlite-vec is not installed") from exc
    path = Path(db_path)
    try:
        if read_only:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        else:
            conn = sqlite3.connect(str(path), timeout=10)
    except sqlite3.Error as exc:
        raise VectorIndexUnreadable(f"cannot open {path.name}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.Error) as exc:
        conn.close()
        raise VectorIndexUnreadable(f"sqlite-vec cannot be loaded: {exc}") from exc
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def indexed_fact_ids(conn: sqlite3.Connection, profile_id: str | None = None) -> set[str]:
    """Facts that have a metadata row AND a physical vector for the same profile."""
    if not (_has_table(conn, "fact_embeddings") and _has_table(conn, "embedding_metadata")):
        return set()
    if profile_id is None:
        rows = conn.execute(
            "SELECT em.fact_id FROM embedding_metadata em "
            "JOIN fact_embeddings fe ON fe.rowid = em.vec_rowid "
            "AND fe.profile_id = em.profile_id").fetchall()
    else:
        rows = conn.execute(
            "SELECT em.fact_id FROM embedding_metadata em "
            "JOIN fact_embeddings fe ON fe.rowid = em.vec_rowid AND fe.profile_id = ? "
            "WHERE em.profile_id = ?", (profile_id, profile_id)).fetchall()
    return {str(r[0]) for r in rows}


def physical_rowids(conn: sqlite3.Connection, profile_id: str) -> list[int]:
    """Every vec0 row filed under ``profile_id``, mapped or not."""
    if not _has_table(conn, "fact_embeddings"):
        return []
    rows = conn.execute(
        "SELECT rowid FROM fact_embeddings WHERE profile_id = ?", (profile_id,)).fetchall()
    return [int(r[0]) for r in rows]


def purge_profile(db_path: str | Path, profile_id: str) -> VectorPurge:
    """Remove every vector, and its bookkeeping, filed under ``profile_id``.

    ``remaining`` is read back after the delete, from the index itself: a sweep
    that reports what it meant to do is how an erasure comes to claim a thing it
    did not do. Raises :class:`VectorIndexUnreadable` when the index exists but
    cannot be opened, because "could not look" must not read as "nothing there".
    """
    path = Path(db_path)
    if not path.exists():
        return VectorPurge(no_index=True)
    probe = sqlite3.connect(str(path), timeout=10)
    try:
        has_index = _has_table(probe, "fact_embeddings")
        has_map = _has_table(probe, "vector_row_map") or _has_table(probe, "embedding_metadata")
    finally:
        probe.close()
    if not has_index and not has_map:
        return VectorPurge(no_index=True)
    if not has_index:
        return _purge_bookkeeping_only(path, profile_id)
    conn = open_vec(path)
    try:
        rowids = set(physical_rowids(conn, profile_id))
        for table in ("embedding_metadata", "vector_row_map"):
            if _has_table(conn, table):
                rowids |= {int(r[0]) for r in conn.execute(
                    f"SELECT vec_rowid FROM {table} WHERE profile_id = ?", (profile_id,))}
        ordered = sorted(rowids)
        for start in range(0, len(ordered), _CHUNK):
            chunk = ordered[start:start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM fact_embeddings WHERE rowid IN ({marks})", chunk)
        for table in ("embedding_metadata", "vector_row_map"):
            if _has_table(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,))
        conn.commit()
        left = len(physical_rowids(conn, profile_id))
        return VectorPurge(removed=len(ordered), remaining=left)
    finally:
        conn.close()


def purge_facts(db_path: str | Path, fact_ids: list[str]) -> VectorPurge:
    """Remove the vectors of these facts from the index and their bookkeeping rows.

    For erasure made without an engine (no ``VectorStore``) and for replaying an
    erasure into a restored database. Reads the index back; raises
    :class:`VectorIndexUnreadable` when an index exists that cannot be opened.
    """
    path = Path(db_path)
    ids = sorted({str(f) for f in fact_ids if f})
    if not ids or not path.exists():
        return VectorPurge(no_index=not path.exists())
    probe = sqlite3.connect(str(path), timeout=10)
    try:
        has_index = _has_table(probe, "fact_embeddings")
        has_map = _has_table(probe, "vector_row_map") or _has_table(probe, "embedding_metadata")
    finally:
        probe.close()
    if not has_index and not has_map:
        return VectorPurge(no_index=True)
    conn = open_vec(path) if has_index else sqlite3.connect(str(path), timeout=10)
    try:
        rowids: set[int] = set()
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for table in ("embedding_metadata", "vector_row_map"):
                if _has_table(conn, table):
                    rowids |= {int(r[0]) for r in conn.execute(
                        f"SELECT vec_rowid FROM {table} WHERE fact_id IN ({marks})", chunk)}
        ordered = sorted(rowids)
        if has_index:
            for start in range(0, len(ordered), _CHUNK):
                part = ordered[start:start + _CHUNK]
                conn.execute(
                    f"DELETE FROM fact_embeddings WHERE rowid IN ({','.join('?' * len(part))})",
                    part)
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for table in ("embedding_metadata", "vector_row_map"):
                if _has_table(conn, table):
                    conn.execute(f"DELETE FROM {table} WHERE fact_id IN ({marks})", chunk)
        conn.commit()
        left = 0
        if has_index and ordered:
            marks = ",".join("?" * len(ordered))
            left = int(conn.execute(
                f"SELECT COUNT(*) FROM fact_embeddings WHERE rowid IN ({marks})",
                ordered).fetchone()[0])
        return VectorPurge(removed=len(ordered), remaining=left)
    finally:
        conn.close()


def _purge_bookkeeping_only(path: Path, profile_id: str) -> VectorPurge:
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        removed = 0
        for table in ("embedding_metadata", "vector_row_map"):
            if _has_table(conn, table):
                removed += conn.execute(
                    f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,)).rowcount
        conn.commit()
        return VectorPurge(removed=removed)
    finally:
        conn.close()


__all__ = [
    "VectorIndexUnreadable",
    "VectorPurge",
    "indexed_fact_ids",
    "open_vec",
    "physical_rowids",
    "purge_facts",
    "purge_profile",
]
