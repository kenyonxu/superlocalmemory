# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Ground entity fields without a second call to the generating model."""

from __future__ import annotations

import re
from dataclasses import replace

from superlocalmemory.encoding.assertion_support import words
from superlocalmemory.encoding.speaker_spans import normalized_spans
from superlocalmemory.encoding.typed_values import bare_numbers


def grounded_entities(facts, source: str):
    supported = " ".join(normalized_spans(source))
    return [
        replace(
            fact,
            entities=[
                entity
                for entity in fact.entities
                if re.search(r"(?<!\w)" + re.escape(entity) + r"(?!\w)", supported, re.I)
            ],
        )
        for fact in facts
    ]


def fact_key(content: str) -> tuple:
    """Equivalent wording only; amounts, negation, quotes and names stay distinct."""
    from superlocalmemory.encoding.speaker_spans import quoted_texts

    return (
        tuple(sorted(words(content))),
        tuple(sorted(bare_numbers(content))),
        tuple(quoted_texts(content)),
        _actor_signature(content),
    )


def supported_facts(facts, source: str, reference_date: str | None = None):
    from superlocalmemory.encoding.source_fidelity import check_fact_against_source

    return grounded_entities(
        [
            fact
            for fact in facts
            if check_fact_against_source(fact.content, source, reference_date=reference_date).ok
        ],
        source,
    )


def collapse_partials(facts):
    """Discard redundant partial paraphrases; never conflate differing values or negation."""
    kept = []
    for fact in sorted(facts, key=lambda f: len(f.content), reverse=True):
        key = fact_key(fact.content)
        tokens, values, quotes = map(set, key[:3])
        redundant = False
        for other in kept:
            old_key = fact_key(other.content)
            old_tokens, old_values, old_quotes = map(set, old_key[:3])
            same_negation = (tokens & {"not", "never", "without", "cannot"}) == (
                old_tokens & {"not", "never", "without", "cannot"}
            )
            if (
                same_negation
                and key[3] == old_key[3]
                and tokens <= old_tokens
                and values <= old_values
                and quotes == old_quotes
                and len(tokens) / max(1, len(old_tokens)) >= 0.65
            ):
                redundant = True
                break
        if not redundant:
            kept.append(fact)
    return kept


def _actor_signature(content: str):
    """Keep different speakers and reversed named relations separate."""
    from superlocalmemory.encoding.speaker_spans import outside_quotes

    chunks = []
    outside_quotes(content, lambda text: chunks.append(text) or text)
    plain = " ".join(chunks)
    if re.search(r"\bthe user\b", plain, re.I):
        actor = "user"
    elif match := re.match(
        r"([A-Za-z][\w ]*?)\s+(?:works?|uses?|prefers?|owns?|"
        r"manages?|is|are|was|were|has|have|said|will)\b",
        plain,
        re.I,
    ):
        actor = re.sub(r"^the\s+", "", match.group(1).strip().lower())
    else:
        actor = ""
    relation = tuple(re.findall(r"\b([A-Z][a-z]+) (?:manages?|owns?|leads?) ([A-Z][a-z]+)", plain))
    return actor, relation
