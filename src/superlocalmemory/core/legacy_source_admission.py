# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""The legacy fast writer joins the same atomic source-identity contract."""

from __future__ import annotations

from superlocalmemory.core.queryable_fact import queryable_fact
from superlocalmemory.core.source_excerpt import attach_source_pointer
from superlocalmemory.storage.source_identity import reuse_source


def admit_source(db, record, fact_text: str, now: str, max_chars: int,
                 provenance_kind: str | None = None):
    fact = queryable_fact(
        fact_text,
        profile_id=record.profile_id,
        scope=record.scope,
        shared_with=record.shared_with,
        session_id=record.session_id,
        observation_date=record.session_date,
        created_at=now,
    )
    # Governance tag (spec section 4), ported from the fork's store_fast
    # construction: same leniency as canonical_store — an out-of-vocabulary
    # value files as untagged, never fails the write.
    if provenance_kind:
        from superlocalmemory.storage.models import validate_provenance_kind
        fact.provenance_kind = validate_provenance_kind(provenance_kind)
    with db.transaction():
        reused = reuse_source(db, record, record.metadata, fact)
        if reused:
            record.memory_id = reused
        else:
            db.store_memory(record)
        fact.memory_id = record.memory_id
        if fact_text != record.content:
            fact = attach_source_pointer(fact, record.content, max_chars)
        db.store_fact(fact)
    return fact
