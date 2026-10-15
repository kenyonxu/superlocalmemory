# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com | https://varunpratap.com

"""A targeted erasure outlives the backup that still holds what it erased.

Erasing one memory (or one person's mentions, or a document's pages) deletes it
from the live store, and the proof that it happened lives in that same store, so
restoring an earlier backup brings the memory back WITH the state in which it had
not yet been erased. A profile erasure already avoided this with an obligation
ledger kept outside every backup. A targeted erasure did not: its restore came
back with the erased facts active and in ordinary recall results, and nothing in
the ledger said otherwise.

This is the same ledger (``backup_obligations.db``, never part of a backup set),
keyed on the erased subject instead of the profile:

* ``fact``: a fact id;
* ``entity``: a canonical entity id (its name is the erased words).

Registration happens when the erasure receipt is written
(:func:`register_targeted_erasure`), and says which snapshots still hold the
subject, so the receipt is not called complete while they do. A restore
replays every entry into the restored database before anything reads it
(:func:`replay_into`). The rest of the same profile is untouched.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

_LEDGER = "backup_obligations.db"
_CHUNK = 400

_DDL = """
CREATE TABLE IF NOT EXISTS erased_subjects (
    subject_id  TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('fact', 'entity')),
    target_id   TEXT NOT NULL,
    erasure_id  TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    UNIQUE (profile_id, kind, target_id)
);
CREATE INDEX IF NOT EXISTS idx_erased_subjects_profile ON erased_subjects(profile_id);
"""

#: Columns that name a fact (or a fact-keyed node) in the tables of a store.
_FACT_COLUMNS = ("fact_id", "source_id", "target_id", "source_fact_id", "target_fact_id", "node_id")
#: Tables that record an erasure or a profile; never deleted by a replay.
_LEDGERS = frozenset({"erasure_receipts", "projection_tombstones", "profiles", "audit_chain",
                      "compliance_audit"})


@dataclass(frozen=True)
class Subject:
    profile_id: str
    kind: str
    target_id: str


def _connect(data_root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(data_root) / _LEDGER), timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
    except BaseException:
        conn.close()
        raise
    return conn


def record(data_root: Path, profile_id: str, kind: str, targets: Iterable[str],
           erasure_id: str) -> int:
    """Write the subjects to the ledger. Raises when they cannot be written."""
    ids = sorted({str(t) for t in targets if t})
    if not ids:
        return 0
    conn = _connect(data_root)
    try:
        now = time.time()
        conn.executemany(
            "INSERT OR IGNORE INTO erased_subjects "
            "(subject_id, profile_id, kind, target_id, erasure_id, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(uuid.uuid4().hex, profile_id, kind, t, erasure_id, now) for t in ids])
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def list_subjects(data_root: Path, profile_id: str | None = None) -> list[Subject]:
    if not (Path(data_root) / _LEDGER).exists():
        return []
    conn = _connect(data_root)
    try:
        if profile_id is None:
            rows = conn.execute("SELECT profile_id, kind, target_id FROM erased_subjects")
        else:
            rows = conn.execute(
                "SELECT profile_id, kind, target_id FROM erased_subjects WHERE profile_id = ?",
                (profile_id,))
        return [Subject(r["profile_id"], r["kind"], r["target_id"]) for r in rows.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------- registration

def snapshots_holding_facts(data_root: Path, fact_ids: Sequence[str]) -> list[str]:
    """Snapshot files (by name) that still hold any of these facts, or cannot be read."""
    from superlocalmemory.compliance.erasure_backups import SNAPSHOT_DIRS
    from superlocalmemory.infra.backup_obligations import _DB_SUFFIXES

    ids = sorted({str(f) for f in fact_ids if f})
    holding: list[str] = []
    for folder in (*SNAPSHOT_DIRS, "pre-restore"):
        root = Path(data_root) / folder
        if not root.is_dir() or root.is_symlink():
            continue
        for entry in sorted(root.iterdir()):
            files = ([f for f in sorted(entry.iterdir()) if f.suffix in _DB_SUFFIXES]
                     if entry.is_dir() else [entry] if entry.suffix in _DB_SUFFIXES else [])
            if any(_snapshot_holds(f, ids) is not False for f in files):
                holding.append(f"{folder}/{entry.name}")
    return holding


def _snapshot_holds(path: Path, ids: list[str]) -> bool | None:
    """True/False once read; ``None`` when the snapshot cannot be read (not shown clean)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='atomic_facts'").fetchone()
        if has is None:
            return False
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            row = conn.execute(
                f"SELECT 1 FROM atomic_facts WHERE fact_id IN ({','.join('?' * len(chunk))}) "
                "LIMIT 1", chunk).fetchone()
            if row is not None:
                return True
        return False
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def register_targeted_erasure(db: object, profile_id: str, fact_ids: Sequence[str],
                              erasure_id: str) -> tuple[bool, list[str]]:
    """Journal a fact erasure outside every backup; report snapshots still holding it.

    Returns ``(journalled, snapshots)``. A database with no file behind it (a
    test double) has no backups to protect: ``(True, [])``.
    """
    db_path = getattr(db, "db_path", None)
    if db_path is None or not fact_ids:
        return True, []
    root = Path(db_path).resolve().parent
    if not root.is_dir():  # no data folder, so no store and no backup to protect
        return True, []
    try:
        record(root, profile_id, "fact", fact_ids, erasure_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("erasure journal could not be written: %s", type(exc).__name__)
        return False, []
    return True, snapshots_holding_facts(root, fact_ids)


# ---------------------------------------------------------------------- replay

def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')]
    except sqlite3.Error:
        return []  # a virtual table whose module is not loaded here


def _delete_in(conn: sqlite3.Connection, table: str, column: str, ids: list[str]) -> int:
    removed = 0
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        removed += conn.execute(
            f'DELETE FROM "{table}" WHERE "{column}" IN ({",".join("?" * len(chunk))})',
            chunk).rowcount
    return removed


def _erase_facts_rows(conn: sqlite3.Connection, fact_ids: list[str]) -> int:
    memory_ids: set[str] = set()
    if "atomic_facts" in _tables(conn):
        for start in range(0, len(fact_ids), _CHUNK):
            chunk = fact_ids[start:start + _CHUNK]
            memory_ids |= {r[0] for r in conn.execute(
                f"SELECT DISTINCT memory_id FROM atomic_facts WHERE fact_id IN "
                f"({','.join('?' * len(chunk))})", chunk) if r[0]}
    removed = 0
    for table in sorted(_tables(conn) - _LEDGERS):
        cols = _columns(conn, table)
        for column in _FACT_COLUMNS:
            if column in cols and not (table == "code_memory_links" and column != "fact_id"):
                removed += _delete_in(conn, table, column, fact_ids)
    if memory_ids and {"memories", "atomic_facts"} <= _tables(conn):
        for mid in memory_ids:
            if conn.execute("SELECT 1 FROM atomic_facts WHERE memory_id = ? LIMIT 1",
                            (mid,)).fetchone() is None:
                removed += conn.execute("DELETE FROM memories WHERE memory_id = ?",
                                        (mid,)).rowcount
    return removed


def _blank_journal_text(conn: sqlite3.Connection, fact_ids: list[str]) -> int:
    """The ingestion journal keeps a memory's full original text; blank it for erased facts."""
    cols = _columns(conn, "ingestion_operations")
    if not {"raw_content", "final_fact_ids_json"} <= set(cols):
        return 0
    from superlocalmemory.core.erasure_scrub import ERASED_NOTE

    blanked = 0
    for fid in fact_ids:
        blanked += conn.execute(
            "UPDATE ingestion_operations SET raw_content = ? "
            "WHERE final_fact_ids_json LIKE ? AND raw_content <> ?",
            (ERASED_NOTE, f'%"{fid}"%', ERASED_NOTE)).rowcount
    return blanked


def _erase_entity_rows(conn: sqlite3.Connection, entity_ids: list[str]) -> int:
    removed = 0
    for table in sorted(_tables(conn) - _LEDGERS):
        cols = _columns(conn, table)
        if "entity_id" in cols:
            removed += _delete_in(conn, table, "entity_id", entity_ids)
    return removed


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _purge_keyword_indexes(conn: sqlite3.Connection) -> None:
    """The keyword index keeps a deleted row's words in its segments until rewritten."""
    from superlocalmemory.storage import fts_residue

    present = _tables(conn)
    for table in fts_residue.FTS_TABLES:
        if table in present:
            fts_residue.purge_deleted_terms(conn, table)


def _erase_through_the_erasure_code(db_path: Path, subjects: Sequence[Subject]) -> None:
    """Run the real fact erasure on the restored file.

    The rows that name a fact are only part of what a memory leaves: its entity
    summaries, scenes, journal text and unnamed entities are rebuilt or removed
    by the erasure code, which can still read the fact because the restored
    store has it. Doing it here, and not a second copy of that logic, is what
    keeps a replay as complete as the original erasure.
    """
    from collections import defaultdict

    from superlocalmemory.compliance.gdpr import GDPRCompliance
    from superlocalmemory.storage.database import DatabaseManager

    by_profile: dict[str, list[str]] = defaultdict(list)
    for subject in subjects:
        if subject.kind == "fact":
            by_profile[subject.profile_id].append(subject.target_id)
    if not by_profile:
        return
    db = DatabaseManager(db_path)
    try:
        for profile_id, ids in by_profile.items():
            GDPRCompliance(db, data_root=Path(db_path).parent).forget_facts(
                ids, profile_id, subject_id="restore-replay")
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


def replay_into(db_path: Path, subjects: Sequence[Subject]) -> dict[str, int]:
    """Erase every journalled subject from a (restored) memory database.

    Raises when it cannot, so the restore is reported as not clean: a restore
    that resurrects an erased memory is the failure this exists to stop.
    """
    facts = sorted({s.target_id for s in subjects if s.kind == "fact"})
    entities = sorted({s.target_id for s in subjects if s.kind == "entity"})
    out = {"facts": 0, "entities": 0}
    if not (facts or entities) or not Path(db_path).exists():
        return out
    probe = sqlite3.connect(str(db_path), timeout=30)
    try:
        if "atomic_facts" not in _tables(probe):  # nothing of a memory store in this file
            return out
    finally:
        probe.close()
    _erase_through_the_erasure_code(Path(db_path), subjects)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        if "atomic_facts" not in _tables(conn):
            return out
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        try:
            out["facts"] = _erase_facts_rows(conn, facts) if facts else 0
            if facts:
                _blank_journal_text(conn, facts)
            out["entities"] = _erase_entity_rows(conn, entities) if entities else 0
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        _purge_keyword_indexes(conn)
    finally:
        conn.close()
    if facts:
        from superlocalmemory.retrieval import vector_physical

        left = vector_physical.purge_facts(db_path, facts)
        if left.remaining:
            raise RuntimeError("erased vectors are still in the restored index")
    from superlocalmemory.infra import sqlite_scrub

    if not sqlite_scrub.scrub(db_path):
        raise RuntimeError("the restored database could not be rewritten after the replay")
    return out


def replay_all(data_root: Path, restored_db_paths: Iterable[Path]) -> dict[str, int]:
    """Apply the whole journal to each restored memory database."""
    subjects = list_subjects(data_root)
    totals = {"facts": 0, "entities": 0}
    if not subjects:
        return totals
    for path in restored_db_paths:
        if Path(path).name != "memory.db":
            continue
        done = replay_into(Path(path), subjects)
        totals["facts"] += done["facts"]
        totals["entities"] += done["entities"]
    return totals
