# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Supported statements plus bounded review evidence, scoped to one extraction.

Iteration and truthiness describe supported facts only. Rejected statements
are carried separately so a store can quarantine them before enrichment.
No extractor instance state is used: concurrent saves cannot exchange candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from superlocalmemory.encoding.source_fidelity import check_fact_against_source
from superlocalmemory.storage.models import AtomicFact

# Five bounded source chunks, with at most ten parsed statements per chunk.
MAX_REVIEW_CANDIDATES = 50


@dataclass
class ReviewFact(AtomicFact):
    """An extraction failure the content-only checker cannot reconstruct."""

    fidelity_reasons: tuple[str, ...] = ()


def for_review(fact: AtomicFact, reasons: tuple[str, ...]) -> ReviewFact:
    return ReviewFact(**{f.name: getattr(fact, f.name) for f in fields(AtomicFact)},
                      fidelity_reasons=reasons)


class FactExtraction(list[AtomicFact]):
    """List-compatible trusted result with a separate, immutable review tuple."""

    def __init__(self, facts=(), review_candidates=()):
        super().__init__(facts)
        seen: set[str] = set()
        candidates = []
        for fact in review_candidates:
            if fact.content not in seen:
                seen.add(fact.content)
                candidates.append(fact)
            if len(candidates) == MAX_REVIEW_CANDIDATES:
                break
        self.review_candidates = tuple(candidates)


def partition_facts(facts, source: str, reference_date: str | None = None):
    """Keep rejected assertions outside the iterable used for live enrichment."""
    live, review = [], list(getattr(facts, "review_candidates", ()))
    for fact in facts:
        report = check_fact_against_source(fact.content, source, reference_date=reference_date)
        if report.ok and not getattr(fact, "fidelity_reasons", ()):
            live.append(fact)
        else:
            review.append(
                fact if isinstance(fact, ReviewFact) else for_review(fact, report.reasons))
    return FactExtraction(live, review)
