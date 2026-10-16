# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Retention mutations recheck protection under SQLite write authority."""

from contextlib import contextmanager, nullcontext

from superlocalmemory.core.lifecycle_state import atomic_lifecycle_for, set_fact_lifecycle_zone
from superlocalmemory.learning.forgetting_metadata import protected_fact


@contextmanager
def lifecycle_write(db):
    conn = getattr(db._txn_state, "conn", None)
    with nullcontext() if conn is not None else db.transaction():
        # DatabaseManager.transaction defers BEGIN until DML. Reserve the writer
        # before reading protection, including when called inside an outer txn.
        if not db._txn_state.conn.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        yield


def batch_retention(db, facts, profile_id):
    count = 0
    with lifecycle_write(db):
        for fact in facts:
            if protected_fact(db, fact["fact_id"], profile_id):
                continue
            db._upsert_retention_in_transaction(
                fact_id=fact["fact_id"], profile_id=profile_id,
                retention_score=fact["retention"], memory_strength=fact["strength"],
                access_count=fact["access_count"], last_accessed_at=fact["last_accessed_at"],
                lifecycle_zone=fact["zone"],
            )
            db.execute(
                "UPDATE atomic_facts SET lifecycle=? WHERE fact_id=? AND profile_id=?",
                (atomic_lifecycle_for(fact["zone"]), fact["fact_id"], profile_id),
            )
            count += 1
    return count


def forget_fact(db, fact_id, profile_id):
    with lifecycle_write(db):
        if protected_fact(db, fact_id, profile_id):
            return
        if not db.execute(
            "SELECT 1 FROM fact_retention WHERE fact_id=? AND profile_id=?",
            (fact_id, profile_id),
        ):
            return
        set_fact_lifecycle_zone(db, [fact_id], "forgotten", profile_id=profile_id)
        db.execute(
            "UPDATE fact_retention SET retention_score=0 WHERE fact_id=? AND profile_id=?",
            (fact_id, profile_id),
        )


def compact_facts(db, candidates, profile_id):
    applied = []
    with lifecycle_write(db):
        for candidate in candidates:
            if protected_fact(db, candidate["fact_id"], profile_id):
                continue
            if set_fact_lifecycle_zone(
                db, [candidate["fact_id"]], candidate["proposed"],
                profile_id=profile_id, from_atomic=[candidate["current"]],
            ):
                applied.append(candidate)
    return applied
