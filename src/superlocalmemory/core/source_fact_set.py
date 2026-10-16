# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Keep the source projection outside the bounded derived-fact set."""

from __future__ import annotations

from superlocalmemory.encoding.fact_grounding import supported_facts
from superlocalmemory.storage.models import AtomicFact, FactType


def source_fact_set(facts, content, record, queryable_facts, max_chars=24000, *, db=None,
                    max_derived=10, coverage_limit=40):
    """Supported derivatives, a verbatim fact per sentence they miss, and the source."""
    raw = content.strip().casefold()
    derived = [fact for fact in facts if fact.content.strip().casefold() != raw]
    from superlocalmemory.encoding.speaker_spans import with_actor

    context = with_actor(content, record.speaker, record.role)
    from superlocalmemory.core.source_fidelity_quarantine import store_review_candidates
    from superlocalmemory.encoding.fact_extraction_result import FactExtraction, partition_facts

    checked = partition_facts(FactExtraction(
        derived, getattr(facts, "review_candidates", ())), context, record.session_date)
    store_review_candidates(db, checked.review_candidates, record)
    derived = supported_facts(checked, context, record.session_date)[:max_derived]
    from superlocalmemory.encoding.coverage_facts import coverage_facts

    derived += coverage_facts(content, derived, session_id=record.session_id,
                              session_date=record.session_date, limit=coverage_limit)
    if queryable_facts:
        originals = list(queryable_facts)
    else:
        from superlocalmemory.core.ingest_gate import apply_ingest_gate
        from superlocalmemory.core.source_excerpt import attach_source_pointer
        from superlocalmemory.encoding.entity_mentions import extract_mentions

        gate = apply_ingest_gate(content, max_verbatim_chars=max_chars)
        source = AtomicFact(
            memory_id=record.memory_id,
            content=gate.fact_content,
            profile_id=record.profile_id,
            scope=record.scope,
            shared_with=record.shared_with,
            entities=extract_mentions(content),
            fact_type=FactType.EPISODIC,
            observation_date=record.session_date,
            session_id=record.session_id,
            confidence=0.9,
            importance=0.5,
        )
        originals = [
            attach_source_pointer(source, content, max_chars) if gate.truncated else source
        ]
    protected = frozenset(fact.fact_id for fact in originals)
    return [fact for fact in derived if fact.fact_id not in protected] + originals, protected
