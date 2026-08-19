#!/usr/bin/env python3
"""Idempotent, resumable backfill: atomic_facts.embedding TEXT → BLOB float32.

Run against a database while the daemon is idle.  A 0.05-second pause between
batches yields to any concurrent readers using WAL mode.

Usage
-----
    python scripts/backfill_embeddings.py [--db PATH] [--batch N] [--dry-run]

    --db PATH       Path to memory.db (default: ~/.superlocalmemory/memory.db).
    --batch N       Rows per transaction (default: 200).
    --dry-run       Print the count and exit; touch nothing.

Safety
------
- Idempotent: rows already stored as BLOB are skipped via
  ``WHERE typeof(embedding)='text'``.
- Resumable: on restart the same WHERE clause picks up where the previous run
  left off.
- Batch-transactional: each batch is its own BEGIN/COMMIT so a crash mid-run
  does not leave a partial transaction open.
- Non-null count invariant: the script converts only TEXT rows and never
  modifies NULL rows, so the non-null count before and after must be equal.
- Dimension assertion: every decoded embedding must be exactly 768 floats;
  any fact whose stored JSON does not decode to 768 floats is logged and
  skipped (not silently dropped).
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Imported rather than redeclared: a second copy of the format constants is a
# second thing to forget when the format changes.
from superlocalmemory.storage.embedding_codec import (  # noqa: E402
    EMBEDDING_BYTES as _EMBEDDING_BYTES,
    EMBEDDING_DIM as _EMBEDDING_DIM,
)
_DEFAULT_DB = Path.home() / ".superlocalmemory" / "memory.db"
_DEFAULT_BATCH = 200
_BATCH_SLEEP = 0.05  # seconds between batches


def backfill(
    db_path: Path,
    batch_size: int = _DEFAULT_BATCH,
    dry_run: bool = False,
) -> int:
    """Convert all TEXT-format embeddings in *db_path* to binary float32 BLOBs.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    batch_size:
        Number of rows converted per transaction.
    dry_run:
        When True, print the count of TEXT rows and return without writing.

    Returns
    -------
    int
        Number of rows actually converted (0 in dry-run mode).

    Raises
    ------
    RuntimeError
        If the database file is not found.
    """
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM atomic_facts WHERE typeof(embedding)='text'"
        ).fetchone()[0]

        logger.info("TEXT embeddings remaining: %d", remaining)

        if dry_run:
            logger.info("Dry-run mode — no changes written.")
            return 0

        if remaining == 0:
            logger.info("Nothing to convert.")
            return 0

        # Load all TEXT embedding row-IDs and raw values in one read.
        # Ordered by rowid for deterministic resume: if the run is interrupted,
        # the next run will process the same rows in the same order.
        rows = conn.execute(
            "SELECT fact_id, embedding FROM atomic_facts "
            "WHERE typeof(embedding)='text' ORDER BY rowid"
        ).fetchall()

        converted = 0
        skipped = 0

        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            updates: list[tuple[bytes, str]] = []

            for row in batch:
                fact_id: str = row["fact_id"]
                raw: str = row["embedding"]

                try:
                    vec = json.loads(raw)
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.error(
                        "SKIP %s: invalid JSON — %s", fact_id, exc
                    )
                    skipped += 1
                    continue

                if not isinstance(vec, list) or len(vec) != _EMBEDDING_DIM:
                    logger.error(
                        "SKIP %s: expected %d-dim vector, got %s (len=%s)",
                        fact_id, _EMBEDDING_DIM,
                        type(vec).__name__,
                        len(vec) if isinstance(vec, list) else "?",
                    )
                    skipped += 1
                    continue

                blob = np.array(vec, dtype=np.float32).tobytes()
                assert len(blob) == _EMBEDDING_BYTES  # dimension guard
                updates.append((blob, fact_id))

            if updates:
                conn.execute("BEGIN")
                conn.executemany(
                    "UPDATE atomic_facts SET embedding=? WHERE fact_id=?",
                    updates,
                )
                conn.execute("COMMIT")
                converted += len(updates)
                logger.info(
                    "Progress: %d/%d converted (%d skipped)",
                    converted, remaining, skipped,
                )

            time.sleep(_BATCH_SLEEP)

        logger.info(
            "Done. Converted %d rows; skipped %d rows.", converted, skipped
        )
        return converted

    finally:
        conn.close()


def _writer_lock_held(db_path: Path) -> Path | None:
    """A live writer's lock file beside the store, if there is one.

    Rewriting every row of a store a running service is writing to is a way to
    lose data that looks like a successful run.
    """
    lock = db_path.with_name(db_path.name + ".writer.lock")
    return lock if lock.exists() else None


def _snapshot_before_writing(db_path: Path) -> Path:
    """Copy the store aside and verify the copy before a single row is rewritten.

    This rewrites every embedded row in place. An in-place rewrite with no
    verified copy is a deletion that has not happened yet.
    """
    from superlocalmemory.storage.backup import _pre_migration_backup

    root = _pre_migration_backup(
        db_path.with_name("learning.db"), db_path,
        backups_root=db_path.parent / "pre-migration-snapshots",
    )
    # That call returns the DIRECTORY it wrote into, not a file. Select this
    # store's own newest snapshot: the directory also holds the learning-plane
    # snapshot, and checking that one against this store would compare two
    # unrelated databases and call the mismatch a failure.
    candidates = sorted(root.glob(f"{db_path.stem}-*-pre-migration.db"))
    if not candidates:
        raise RuntimeError(f"no snapshot for {db_path.name} was written into {root}")
    snapshot = candidates[-1]
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"snapshot {snapshot} failed its integrity check")
        copied = conn.execute("SELECT COUNT(*) FROM atomic_facts").fetchone()[0]
    finally:
        conn.close()
    live = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        original = live.execute("SELECT COUNT(*) FROM atomic_facts").fetchone()[0]
    finally:
        live.close()
    if copied != original:
        raise RuntimeError(
            f"snapshot has {copied} facts but the store has {original} — "
            f"refusing to rewrite a store whose copy does not match it"
        )
    # Reading a write-ahead-log database materialises companions beside it that a
    # read-only connection cannot remove. Leaving them turns a directory of
    # snapshots into a directory of snapshots and debris.
    for suffix in ("-wal", "-shm"):
        snapshot.with_name(snapshot.name + suffix).unlink(missing_ok=True)
    logger.info("verified snapshot: %s (%d facts)", snapshot, copied)
    return snapshot


def _confirm(db_path: Path, assume_yes: bool) -> None:
    """Get consent before rewriting a store in place."""
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"refusing to rewrite {db_path} without confirmation. "
            f"Re-run with --yes if that is what you intend."
        )
    print(f"\nThis rewrites every embedded row in {db_path} in place.")
    print("A verified copy is taken first and left in pre-migration-snapshots/.")
    if input("Type the database filename to continue: ").strip() != db_path.name:
        raise RuntimeError("name did not match — nothing was written")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help=f"Path to memory.db (default: {_DEFAULT_DB})",
    )
    p.add_argument(
        "--batch",
        type=int,
        default=_DEFAULT_BATCH,
        help=f"Rows per transaction (default: {_DEFAULT_BATCH})",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt (required when not on a terminal)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print row count and exit without writing",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    try:
        if not args.dry_run:
            held = _writer_lock_held(args.db)
            if held is not None:
                raise RuntimeError(
                    f"{held.name} is present — a service still holds {args.db}. "
                    f"Stop it before rewriting the store."
                )
            _confirm(args.db, args.yes)
            _snapshot_before_writing(args.db)
        count = backfill(args.db, batch_size=args.batch, dry_run=args.dry_run)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    sys.exit(0)
