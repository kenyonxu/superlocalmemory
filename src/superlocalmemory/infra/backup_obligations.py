# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V4 | https://qualixar.com

"""Backup erasure obligation ledger — GDPR Art.17 backup residue tracking.

After Art.17 erasure the live stores are clean, but rotating backup snapshots
still hold the erased personal data.  This module tracks ``outstanding
obligations`` — one per (profile_id, snapshot_path) pair — so that:

  1. The erasure receipt accurately declares completeness as FALSE while any
     snapshot still contains the data (C1 gap closure).

  2. A restore that would resurrect erased data is intercepted and the profile
     data re-erased from the restored store before any caller can read it
     (restore-replay invariant).

  3. An obligation ends only when the snapshot is gone from disk or has been
     re-read and found clean. The retention window makes it OVERDUE (reported,
     never dropped): a backup that still holds a person's data is still
     something to erase after 90 days, and a ledger that forgets it would let
     a restore resurrect that person without a word.

IMPORTANT: backup_obligations.db is intentionally NOT in MANAGED_DATABASES
and is therefore never backed up itself.  This prevents a restore from loading
stale obligation state.
"""

from __future__ import annotations
from superlocalmemory.storage.journal_policy import apply_journal_mode, resolve_journal_mode

import enum
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("superlocalmemory.infra.backup_obligations")

_OBLIGATION_DB_NAME = "backup_obligations.db"

_DDL = """
CREATE TABLE IF NOT EXISTS backup_obligations (
    obligation_id    TEXT PRIMARY KEY,
    profile_id       TEXT NOT NULL,
    erasure_id       TEXT NOT NULL,
    snapshot_path    TEXT NOT NULL,
    snapshot_epoch   INTEGER NOT NULL,
    retention_days   INTEGER NOT NULL DEFAULT 90,
    recorded_at      REAL NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    discharged_at    REAL,
    discharge_reason TEXT,
    kind             TEXT NOT NULL DEFAULT 'contains',
    last_replayed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_bko_profile
    ON backup_obligations(profile_id, status);
CREATE INDEX IF NOT EXISTS idx_bko_snapshot
    ON backup_obligations(snapshot_path, status);
CREATE INDEX IF NOT EXISTS idx_bko_erasure
    ON backup_obligations(erasure_id);
"""


class BackupObligationStore:
    """Persistent ledger of outstanding erasure obligations against snapshots.

    An obligation is created for each backup snapshot that was found to contain
    data for a profile that has since been Art.17-erased from the live stores.
    The obligation is discharged when:
      * The snapshot ages past the configured retention window (auto-discharge).
      * The snapshot is restored and the profile data is re-erased during
        restore-replay (explicit discharge).

    The store lives at ``<data_root>/backup_obligations.db``.  It is a plain
    SQLite file that is NOT in MANAGED_DATABASES and therefore never appears
    inside a backup set.
    """

    def __init__(self, data_root: Path) -> None:
        self._db_path = Path(data_root) / _OBLIGATION_DB_NAME
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Fork (f4dd873c): the journal mode comes from the central
            # policy, not a hardcoded WAL here.
            apply_journal_mode(conn)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
        except BaseException:
            # Setup failed: close it now, or the file stays open (Windows
            # then cannot delete or replace it) for as long as the error lives.
            conn.close()
            raise
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                for stmt in _DDL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
                self._add_missing_columns(conn)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore: schema init failed: %s", exc)

    @staticmethod
    def _add_missing_columns(conn: sqlite3.Connection) -> None:
        """A ledger written by an earlier release gains the columns it lacks (O(1))."""
        have = {row[1] for row in conn.execute("PRAGMA table_info(backup_obligations)")}
        if "kind" not in have:
            conn.execute("ALTER TABLE backup_obligations "
                         "ADD COLUMN kind TEXT NOT NULL DEFAULT 'contains'")
        if "last_replayed_at" not in have:
            conn.execute("ALTER TABLE backup_obligations ADD COLUMN last_replayed_at REAL")

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record(
        self,
        profile_id: str,
        erasure_id: str,
        snapshot_path: str,
        snapshot_epoch: int,
        retention_days: int = 90,
        kind: str = "contains",
    ) -> str:
        """Record one outstanding obligation.  Returns the obligation_id.

        ``kind`` is ``"contains"`` (the snapshot was read and holds the profile)
        or ``"unreadable"`` (it could not be read, so it cannot be shown clean).
        Raises when the obligation cannot be written: a swallowed failure here
        is an erasure that forgets its own backups.
        """
        oid = uuid.uuid4().hex
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT obligation_id FROM backup_obligations WHERE profile_id=? "
                "AND snapshot_path=? AND status='pending'",
                (profile_id, str(snapshot_path))).fetchone()
            if existing is not None:
                return str(existing[0])
            conn.execute(
                "INSERT INTO backup_obligations "
                "(obligation_id, profile_id, erasure_id, snapshot_path, "
                " snapshot_epoch, retention_days, recorded_at, status, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    oid, profile_id, erasure_id, str(snapshot_path),
                    int(snapshot_epoch), int(retention_days), time.time(), kind,
                ),
            )
            conn.commit()
        return oid

    def mark_replayed(self, snapshot_path: str) -> int:
        """Note that a restore of this snapshot re-erased the profile from the live store.

        The obligation stays pending: the SNAPSHOT still holds the data, and the
        next restore of it must replay the erasure again.
        """
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE backup_obligations SET last_replayed_at=? "
                    "WHERE snapshot_path=? AND status='pending'",
                    (time.time(), str(snapshot_path)))
                conn.commit()
                return cur.rowcount
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.mark_replayed: %s", exc)
            return 0

    def discharge(self, obligation_id: str, reason: str) -> None:
        """Mark a single obligation as discharged."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE backup_obligations "
                    "SET status='discharged', discharged_at=?, discharge_reason=? "
                    "WHERE obligation_id=?",
                    (time.time(), reason, obligation_id),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.discharge failed: %s", exc)

    def discharge_for_snapshot(self, snapshot_path: str, reason: str) -> int:
        """Discharge ALL pending obligations for a snapshot path.  Returns count."""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE backup_obligations "
                    "SET status='discharged', discharged_at=?, discharge_reason=? "
                    "WHERE snapshot_path=? AND status='pending'",
                    (time.time(), reason, str(snapshot_path)),
                )
                conn.commit()
                return cur.rowcount
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.discharge_for_snapshot: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def _discharge_removed(self) -> int:
        """Discharge obligations whose snapshot is no longer on disk.

        Verified removal is the one thing read paths may conclude on their own
        (a ``stat``). Age is not: see the module docstring.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT obligation_id, snapshot_path FROM backup_obligations "
                    "WHERE status='pending'").fetchall()
                gone = [r["obligation_id"] for r in rows
                        if not Path(r["snapshot_path"]).exists()]
                now = time.time()
                for oid in gone:
                    conn.execute(
                        "UPDATE backup_obligations SET status='discharged', discharged_at=?, "
                        "discharge_reason='snapshot_removed' WHERE obligation_id=?",
                        (now, oid))
                conn.commit()
                return len(gone)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore._discharge_removed: %s", exc)
            return 0

    def list_overdue(self, profile_id: str | None = None) -> list[dict]:
        """Pending obligations whose retention window has passed: still to be erased."""
        self._discharge_removed()
        now = time.time()
        try:
            with self._connect() as conn:
                sql = ("SELECT * FROM backup_obligations WHERE status='pending' "
                       "AND (snapshot_epoch + retention_days * 86400) < ?")
                params: tuple = (now,)
                if profile_id is not None:
                    sql += " AND profile_id=?"
                    params = (now, profile_id)
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.list_overdue: %s", exc)
            return []

    def reconcile(self, profile_id: str | None = None) -> dict[str, int]:
        """Re-read every pending snapshot; discharge only those PROVEN clean or gone.

        A snapshot that still holds the profile, or cannot be read, stays
        pending. Returns ``{"discharged": n, "still_holding": n, "unreadable": n}``.
        """
        self._discharge_removed()
        out = {"discharged": 0, "still_holding": 0, "unreadable": 0}
        for row in self._pending_rows(profile_id):
            verdict = _verdict_of(Path(row["snapshot_path"]), row["profile_id"])
            if verdict is SnapshotVerdict.CLEAN:
                self.discharge(row["obligation_id"], "snapshot_verified_clean")
                out["discharged"] += 1
            elif verdict is SnapshotVerdict.CONTAINS:
                out["still_holding"] += 1
            else:
                out["unreadable"] += 1
        return out

    def _pending_rows(self, profile_id: str | None) -> list[dict]:
        try:
            with self._connect() as conn:
                if profile_id is None:
                    rows = conn.execute(
                        "SELECT * FROM backup_obligations WHERE status='pending'").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM backup_obligations WHERE status='pending' "
                        "AND profile_id=?", (profile_id,)).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore._pending_rows: %s", exc)
            return []

    def count_pending(self, profile_id: str) -> int:
        """Count pending obligations for *profile_id* (gone snapshots are discharged first).

        Fail-closed: if the store is unreadable, returns 1 to block completeness.
        """
        self._discharge_removed()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM backup_obligations "
                    "WHERE profile_id=? AND status='pending'",
                    (profile_id,),
                ).fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "BackupObligationStore.count_pending failed: %s — "
                "returning 1 to block completeness claim",
                exc,
            )
            return 1  # fail-closed

    def list_pending_for_snapshot(self, snapshot_path: str) -> list[dict]:
        """Return all pending obligations for *snapshot_path*."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM backup_obligations "
                    "WHERE snapshot_path=? AND status='pending'",
                    (str(snapshot_path),),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.list_pending_for_snapshot: %s", exc)
            return []

    def list_pending_for_profile(self, profile_id: str) -> list[dict]:
        """Return pending obligations for *profile_id* (gone snapshots are discharged first)."""
        self._discharge_removed()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM backup_obligations "
                    "WHERE profile_id=? AND status='pending'",
                    (profile_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BackupObligationStore.list_pending_for_profile: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Snapshot helpers (used by gdpr.py and backup.py)
# ---------------------------------------------------------------------------


class SnapshotVerdict(enum.Enum):
    """What reading one snapshot established. ``UNKNOWN`` is not ``CLEAN``."""

    CLEAN = "clean"
    CONTAINS = "contains"
    UNKNOWN = "unknown"


@dataclass
class ScanReport:
    """Snapshots that hold a profile, and snapshots that could not be read."""

    hits: list[tuple[str, int]] = field(default_factory=list)
    unreadable: list[tuple[str, int]] = field(default_factory=list)


#: Tables whose rows name a profile but are the PROOF of its erasure, kept by
#: design in the live store too (receipts, the hash-chained audit trail). A
#: backup holding only these holds none of the person's data.
_PROOF_TABLES = frozenset({"erasure_receipts", "audit_chain"})
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def _table_holds_profile(conn: sqlite3.Connection, table: str, profile_id: str) -> bool:
    cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    if "profile_id" in cols:
        return conn.execute(
            f'SELECT 1 FROM "{table}" WHERE profile_id=? LIMIT 1', (profile_id,),
        ).fetchone() is not None
    # The code graph has no profile column: its repository and symbol names are
    # installation-level personal data, so any content flags it.
    return table == "graph_nodes" and conn.execute(
        'SELECT 1 FROM "graph_nodes" LIMIT 1').fetchone() is not None


def inspect_snapshot(db_path: Path, profile_id: str) -> SnapshotVerdict:
    """Read one snapshot file (read-only) and say whether it holds ``profile_id``.

    Every real table is looked at (virtual ones are indexes over those), not a
    short list: a backup of ``learning.db`` or ``pending.db`` holds a person's
    words just as a backup of ``memory.db`` does. Anything that stops the read
    (a corrupt or encrypted file, a permission error, an unexpected schema) is
    ``UNKNOWN``: an unreadable copy cannot be shown not to hold the data.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        logger.warning("backup snapshot %s cannot be opened: %s", Path(db_path).name, exc)
        return SnapshotVerdict.UNKNOWN
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND sql NOT LIKE 'CREATE VIRTUAL%'").fetchall()]
        for table in tables:
            if table not in _PROOF_TABLES and _table_holds_profile(conn, table, profile_id):
                return SnapshotVerdict.CONTAINS
        return SnapshotVerdict.CLEAN
    except (sqlite3.Error, OSError) as exc:
        logger.warning("backup snapshot %s cannot be read (%s): not treating it as clean",
                       Path(db_path).name, exc)
        return SnapshotVerdict.UNKNOWN
    finally:
        conn.close()


def _verdict_of(snapshot: Path, profile_id: str) -> SnapshotVerdict:
    """Verdict for a snapshot file or a backup-set directory (worst case wins)."""
    files = ([f for f in sorted(snapshot.iterdir()) if f.suffix in _DB_SUFFIXES]
             if snapshot.is_dir() else [snapshot])
    verdicts = [inspect_snapshot(f, profile_id) for f in files]
    if SnapshotVerdict.CONTAINS in verdicts:
        return SnapshotVerdict.CONTAINS
    if SnapshotVerdict.UNKNOWN in verdicts or not verdicts:
        # No database inside (a graph or vector store, say) cannot be shown clean.
        return SnapshotVerdict.UNKNOWN
    return SnapshotVerdict.CLEAN


def scan_backup_snapshots(backup_dir: Path, profile_id: str) -> ScanReport:
    """Scan *backup_dir* for snapshots holding *profile_id*, and note unreadable ones.

    Handles per-file backups (``*.db``) and ``backup_XXXX/`` sets (a directory
    with a ``manifest.json``). A snapshot that cannot be read is reported in
    ``unreadable``, never skipped.
    """
    backup_dir = Path(backup_dir)
    report = ScanReport()
    if not backup_dir.exists():
        return report
    for candidate in sorted(backup_dir.iterdir()):
        if candidate.name.startswith(".") or candidate.is_symlink():
            continue
        is_set = candidate.is_dir() and (candidate / "manifest.json").exists()
        if not (is_set or (candidate.is_file() and candidate.suffix in _DB_SUFFIXES)):
            continue
        verdict = _verdict_of(candidate, profile_id)
        entry = (str(candidate), int(candidate.stat().st_mtime))
        if verdict is SnapshotVerdict.CONTAINS:
            report.hits.append(entry)
        elif verdict is SnapshotVerdict.UNKNOWN:
            report.unreadable.append(entry)
    return report


def scan_backup_snapshots_for_profile(
    backup_dir: Path,
    profile_id: str,
) -> list[tuple[str, int]]:
    """Snapshots that hold *profile_id*, as ``(path, epoch)``.

    Unreadable snapshots are not in this list; use :func:`scan_backup_snapshots`
    to see them. Every caller that records obligations must.
    """
    return scan_backup_snapshots(backup_dir, profile_id).hits


def _snapshot_db_contains_profile(db_path: Path, profile_id: str) -> bool:
    """True only when the file was read and holds the profile (kept for old callers)."""
    return inspect_snapshot(db_path, profile_id) is SnapshotVerdict.CONTAINS


def _is_media_snapshot(db_path: Path) -> bool:
    """True for a copy of media.db (images and documents)."""
    if not Path(db_path).name.startswith("media"):
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='media_items'").fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def erase_profile_from_snapshot(db_path: Path, profile_id: str) -> dict[str, int]:
    """Delete all *profile_id* rows from a snapshot SQLite file.

    Used during restore-replay.  Raises on any fatal error so the caller can
    refuse to mark the restore complete when personal data cannot be purged.
    """
    deleted: dict[str, int] = {}
    if _is_media_snapshot(db_path):  # vectors and originals need the media store's own scrub
        from superlocalmemory.media.erasure import scrub_snapshot

        return scrub_snapshot(db_path, profile_id)
    # isolation_level=None → autocommit so VACUUM can run outside any transaction.
    conn = sqlite3.connect(str(db_path))
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not row[0].startswith("sqlite_")
        ]
        conn.execute("BEGIN")
        for table in tables:
            cols = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "profile_id" not in cols:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE profile_id=?", (profile_id,)
            )
            if cur.rowcount:
                deleted[table] = cur.rowcount
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("COMMIT")
        # VACUUM must run in autocommit (no active transaction).
        conn.execute("VACUUM")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        conn.close()
        raise
    else:
        conn.close()
    return deleted


def erase_code_graph_from_snapshot(db_path: Path) -> dict[str, int]:
    """Wipe all tables from a code_graph.db snapshot (no profile_id column).

    The code graph is installation-level personal data (repo paths, symbol
    names).  On erasure or restore-replay the entire graph is cleared.
    """
    deleted: dict[str, int] = {}
    if not db_path.exists():
        return deleted
    conn = sqlite3.connect(str(db_path))
    conn.isolation_level = None  # autocommit so VACUUM runs outside any transaction
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        conn.execute("BEGIN")
        for table in tables:
            # Skip FTS virtual tables — deleting the base table handles them
            if table.endswith(("_fts", "_fts_data", "_fts_idx", "_fts_content",
                                "_fts_docsize", "_fts_config")):
                continue
            cur = conn.execute(f"DELETE FROM {table}")
            if cur.rowcount:
                deleted[table] = cur.rowcount
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("COMMIT")
        # VACUUM must run in autocommit (no active transaction).
        conn.execute("VACUUM")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        conn.close()
        raise
    else:
        conn.close()
    return deleted
