# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Real source storage for tests of stages downstream of recall fidelity."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.migrations import M018_ingestion_operations, M019_derivation_lineage
from superlocalmemory.storage.models import MemoryRecord, RetrievalResult


@contextmanager
def recall_sources(base_dir: Path, results: list[RetrievalResult], *,
                   db: DatabaseManager | None = None) -> Iterator[DatabaseManager]:
    """Give every candidate its own source, preserving its owner and identity.

    Only the source pointer is assigned. Content, order, scores, evidence and
    result objects stay untouched so downstream assertions retain their force.
    By default each call gets a fresh store, including when memory text changes.
    A borrowed fixture store retains its entities and remains open afterward.
    Stable source ids keep otherwise identical recall snapshots comparable.
    """
    with TemporaryDirectory(prefix="recall-sources-", dir=base_dir) as directory:
        borrowed = db is not None
        db = db if borrowed else DatabaseManager(Path(directory) / "memory.db")
        try:
            db.initialize(schema)
            with db.raw_connection() as conn:
                M018_ingestion_operations.apply(conn)
                M019_derivation_lineage.apply(conn)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(atomic_facts)")}
                if "quarantined" not in columns:
                    conn.execute(
                        "ALTER TABLE atomic_facts ADD COLUMN quarantined INTEGER DEFAULT 0")
            for result in results:
                fact = result.fact
                db.execute("INSERT OR IGNORE INTO profiles (profile_id,name) VALUES (?,?)",
                           (fact.profile_id, fact.profile_id))
                record = MemoryRecord(memory_id=fact.memory_id or f"recall-source-{fact.fact_id}",
                                      content=fact.content, profile_id=fact.profile_id,
                                      scope=fact.scope, shared_with=fact.shared_with)
                db.store_memory(record)
                fact.memory_id = record.memory_id
                db.store_fact(fact)
            yield db
        finally:
            if not borrowed:
                db.close()
