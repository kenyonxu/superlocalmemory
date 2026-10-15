# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A genuine access advances the persistent decay clock immediately."""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def advance_clock(db, fact_id, profile_id, log_id):
    try:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_retention'"
        ):
            return
        with db.transaction():
            db.execute(
                "UPDATE fact_retention SET last_accessed_at=(SELECT accessed_at "
                "FROM fact_access_log WHERE log_id=? AND profile_id=?), "
                "access_count=access_count+1,retention_score=1.0,lifecycle_zone='active' "
                "WHERE fact_id=? AND profile_id=?",
                (log_id, profile_id, fact_id, profile_id),
            )
            db.execute(
                "UPDATE atomic_facts SET lifecycle='active' WHERE fact_id=? "
                "AND profile_id=? AND EXISTS (SELECT 1 FROM fact_retention "
                "WHERE fact_id=? AND profile_id=?)",
                (fact_id, profile_id, fact_id, profile_id),
            )
    except sqlite3.Error as exc:
        logger.warning("Access was logged but decay clock refresh failed: %s", exc)


def record_use_clock(conn, profile_id, fact_ids, outcome_id, stamp):
    """The shared outcome writer owns this transaction and its idempotence."""
    import hashlib

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fact_access_log" not in tables:
        return
    for fact_id in fact_ids:
        log_id = hashlib.sha256(f"use:{profile_id}:{outcome_id}:{fact_id}".encode()).hexdigest()[
            :32
        ]
        inserted = conn.execute(
            "INSERT OR IGNORE INTO fact_access_log "
            "(log_id,fact_id,profile_id,accessed_at,access_type,session_id) "
            "SELECT ?,fact_id,profile_id,?,'auto_invoke','' FROM atomic_facts "
            "WHERE fact_id=? AND profile_id=?",
            (log_id, stamp, fact_id, profile_id),
        ).rowcount
        if inserted and "fact_retention" in tables:
            _refresh_used_fact(conn, fact_id, profile_id, stamp)


def _refresh_used_fact(conn, fact_id, profile_id, stamp):
    conn.execute(
        "UPDATE fact_retention SET last_accessed_at=?,access_count=access_count+1,"
        "retention_score=1.0,lifecycle_zone='active' WHERE fact_id=? AND profile_id=?",
        (stamp, fact_id, profile_id),
    )
    conn.execute(
        "UPDATE atomic_facts SET lifecycle='active' WHERE fact_id=? AND profile_id=? "
        "AND EXISTS (SELECT 1 FROM fact_retention WHERE fact_id=? AND profile_id=?)",
        (fact_id, profile_id, fact_id, profile_id),
    )
