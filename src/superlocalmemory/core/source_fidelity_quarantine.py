# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Persist rejected extraction evidence outside the live enrichment pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from superlocalmemory.encoding.source_fidelity import check_fact_against_source
from superlocalmemory.encoding.speaker_spans import with_actor
from superlocalmemory.storage.erasure_fence import is_erasing


def store_review_candidates(db, candidates, record):
    """Commit each row, quarantine and explanation together; retries lose nothing."""
    if not candidates:
        return
    if db is None:
        raise ValueError("Review candidates require a store for quarantine")
    from superlocalmemory.core.source_fidelity_guard import _has_quarantine_column

    if not _has_quarantine_column(db):
        raise RuntimeError("Source fidelity quarantine is unavailable; migrate the store first")
    source = with_actor(record.content, record.speaker, record.role)
    for candidate in candidates:
        fact_id = hashlib.sha256(
            f"source-fidelity\0{record.memory_id}\0{candidate.content}".encode()
        ).hexdigest()[:32]
        fact = replace(candidate, fact_id=fact_id, memory_id=record.memory_id,
                       profile_id=record.profile_id, scope=record.scope,
                       shared_with=record.shared_with, session_id=record.session_id,
                       observation_date=record.session_date,
                       embedding=None, canonical_entities=[])
        report = check_fact_against_source(fact.content, source, reference_date=record.session_date)
        reasons = tuple(dict.fromkeys(
            (*getattr(candidate, "fidelity_reasons", ()), *report.reasons)))
        if not reasons:
            continue
        reason = "source_fidelity:" + "+".join(reasons)
        db._atomically(lambda: _store_candidate(db, fact, reason))


def _store_candidate(db, fact, reason):
    """Never overwrite an existing candidate or a user's release/rejection decision."""
    if is_erasing(fact.profile_id, fact.fact_id) or db.get_fact(fact.fact_id, fact.profile_id):
        return
    tables = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='projection_tombstones'")
    if tables and db.execute("SELECT 1 FROM projection_tombstones "
                             "WHERE profile_id=? AND fact_id=? LIMIT 1",
                             (fact.profile_id, fact.fact_id)):
        return
    from superlocalmemory.core.derivation_lineage import _record
    from superlocalmemory.core.source_fidelity_guard import withhold_fact

    db.insert_fact_immutable(fact)
    withhold_fact(db, profile_id=fact.profile_id, fact_id=fact.fact_id, reason=reason)
    _record(db, profile_id=fact.profile_id, object_type="fact", object_id=fact.fact_id,
            operation_id=f"source-fidelity:{fact.memory_id}", derivation_version="wp5b",
            source_status="unresolved", unresolved_reason=reason)
