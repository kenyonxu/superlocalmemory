# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""M015 — domain_mapping table and domain_tags columns (memory.db, deferred).

Creates the ``domain_mapping`` table for entity->domain lookups and adds
``domain_tags TEXT`` column to 4 core tables (atomic_facts,
canonical_entities, graph_edges, temporal_events).

Seeds ~50 built-in entity->domain mappings via ``post_ddl_hook`` so the
table is immediately useful after migration.

Deferred because the target core tables are bootstrapped at engine init,
not at migration time. The daemon lifespan calls ``apply_deferred`` right
after engine init so these columns materialise on first boot after upgrade.
"""

from __future__ import annotations

import sqlite3

NAME = "M015_add_domain_tags"
DB_TARGET = "memory"

TABLES = [
    "atomic_facts",
    "canonical_entities",
    "graph_edges",
    "temporal_events",
]

DDL = ";".join(
    [
        "CREATE TABLE IF NOT EXISTS domain_mapping "
        "(entity_name TEXT NOT NULL, domain TEXT NOT NULL, "
        "PRIMARY KEY (entity_name, domain))",
    ]
    + [f"ALTER TABLE {t} ADD COLUMN domain_tags TEXT" for t in TABLES]
)


def verify(conn: sqlite3.Connection) -> bool:
    """Check if migration already applied by inspecting domain_mapping table."""
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='domain_mapping'"
        ).fetchall()
        if not rows:
            return False
        cols = {r[1] for r in conn.execute("PRAGMA table_info(atomic_facts)").fetchall()}
        return "domain_tags" in cols
    except sqlite3.Error:
        return False


def post_ddl_hook(conn: sqlite3.Connection) -> None:
    """Seed domain_mapping with built-in entity->domain entries."""
    from superlocalmemory.storage.seed_domain_mapping import SEED_DOMAIN_MAPPINGS

    conn.executemany(
        "INSERT OR IGNORE INTO domain_mapping (entity_name, domain) VALUES (?, ?)",
        SEED_DOMAIN_MAPPINGS,
    )
    conn.commit()
