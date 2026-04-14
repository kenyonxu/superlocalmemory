# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3.4.11 "Scale-Ready" — Schema Extensions.

New tables for tiered storage + future backend integrations:
  - pinned_facts: User-pinned facts that stay in hot tier forever
  - backend_status: Tracks initialization state of LanceDB/KùzuDB backends
  - fact_consolidations: History of merged/consolidated facts

Design rules (inherited):
  - CREATE IF NOT EXISTS for idempotency
  - profile_id where applicable
  - Never ALTER existing column types

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL — Pinned Facts (user override on lifecycle demotion)
# ---------------------------------------------------------------------------

_PINNED_FACTS_DDL = """
CREATE TABLE IF NOT EXISTS pinned_facts (
    fact_id TEXT PRIMARY KEY,
    profile_id TEXT DEFAULT 'default',
    pinned_at TEXT NOT NULL,
    reason TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pinned_facts_profile
    ON pinned_facts(profile_id);
"""

# ---------------------------------------------------------------------------
# DDL — Backend Status (LanceDB, KùzuDB, sqlite-vec tracking)
# ---------------------------------------------------------------------------

_BACKEND_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS backend_status (
    backend_name TEXT PRIMARY KEY,
    status TEXT DEFAULT 'not_initialized',
    record_count INTEGER DEFAULT 0,
    last_sync_at TEXT,
    error_message TEXT DEFAULT '',
    config TEXT DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# DDL — Fact Consolidations (merge history)
# ---------------------------------------------------------------------------

_FACT_CONSOLIDATIONS_DDL = """
CREATE TABLE IF NOT EXISTS fact_consolidations (
    consolidation_id TEXT PRIMARY KEY,
    profile_id TEXT DEFAULT 'default',
    consolidated_fact_id TEXT NOT NULL,
    source_fact_ids TEXT NOT NULL,
    strategy TEXT DEFAULT 'entity_cluster',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_consolidations_profile
    ON fact_consolidations(profile_id);
CREATE INDEX IF NOT EXISTS idx_fact_consolidations_target
    ON fact_consolidations(consolidated_fact_id);
"""

# ---------------------------------------------------------------------------
# DDL — Index on lifecycle for fast tier queries
# ---------------------------------------------------------------------------

_LIFECYCLE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_atomic_facts_lifecycle
    ON atomic_facts(lifecycle, profile_id);
"""


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def apply_v3411_schema(db_path: str | sqlite3.Connection) -> dict:
    """Apply all v3.4.11 schema changes. Idempotent."""
    result = {"applied": [], "errors": []}

    if isinstance(db_path, sqlite3.Connection):
        conn = db_path
        own_connection = False
    else:
        conn = sqlite3.connect(str(db_path))
        own_connection = True

    try:
        for name, ddl in [
            ("pinned_facts", _PINNED_FACTS_DDL),
            ("backend_status", _BACKEND_STATUS_DDL),
            ("fact_consolidations", _FACT_CONSOLIDATIONS_DDL),
            ("lifecycle_index", _LIFECYCLE_INDEX_DDL),
        ]:
            try:
                conn.executescript(ddl)
                result["applied"].append(name)
            except sqlite3.OperationalError as e:
                result["errors"].append(f"{name}: {e}")

        # Seed default backend entries
        now = __import__("datetime").datetime.now().isoformat()
        for backend in ("sqlite_vec", "sqlite_graph", "lancedb", "kuzu"):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO backend_status "
                    "(backend_name, status, config) VALUES (?, ?, ?)",
                    (backend, "not_initialized" if backend in ("lancedb", "kuzu") else "active", "{}"),
                )
            except sqlite3.OperationalError:
                pass

        # Mark version
        try:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                ("3.4.11", now),
            )
        except sqlite3.OperationalError:
            pass

        conn.commit()

        if result["applied"]:
            logger.info("Schema v3.4.11 applied: %s", ", ".join(result["applied"]))

    except Exception as e:
        result["errors"].append(f"fatal: {e}")
        logger.error("Schema v3.4.11 migration failed: %s", e)
    finally:
        if own_connection:
            conn.close()

    return result
