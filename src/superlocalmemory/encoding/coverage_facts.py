# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Every sentence of a saved memory is findable on its own.

The extractor keeps the few facts it judges most important. The rest of a long
memory lived only inside the whole-memory fact, whose one embedding is
dominated by the memory's opening: a launch note's fifth bullet ("Product Hunt
page fix needed: license MIT to AGPL-3.0.") had no fact of its own and recall
abstained on it (13 Oct 2026).

After extraction, each source sentence or bullet that no extracted fact
already holds becomes a fact of its own, verbatim. Verbatim text cannot
misstate its source, so it needs no model call and no grounding check.
"""

from __future__ import annotations

import re

from superlocalmemory.encoding.fact_extractor import _extract_entities, _is_filler, sentence_fact
from superlocalmemory.encoding.speaker_spans import speaker_spans, split_sentences
from superlocalmemory.storage.models import AtomicFact

#: Facts added per memory at most; a long document keeps its first ones.
DEFAULT_LIMIT = 40
#: A sentence is held by a fact that carries this share of its words.
COVERED_SHARE = 0.8
#: Shorter segments are fragments ("Ok.", "See above."), not facts.
MIN_CHARS = 20

#: A line that is only markup: "<summary>Agent ... completed</summary>", "<task-id>".
_MARKUP = re.compile(r"^<[^>]+>.*</?[\w-]*>?$|^<[^>]+>$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")
_WORD = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*", re.I)
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "then this to was were will with".split()
)


def source_segments(content: str) -> list[str]:
    """Sentences and bullets of *content*, in order; named speakers stay attached."""
    segments: list[str] = []
    for span in speaker_spans(content, ""):
        prefix = f"[{span.speaker}]: " if span.speaker not in ("", "the user") else ""
        for line in span.text.splitlines():
            line = _BULLET.sub("", line).strip()
            if _MARKUP.match(line):
                continue
            segments.extend(prefix + sentence for sentence in split_sentences(line))
    return segments


def _stem(word: str) -> str:
    # "fix needed" and "needs its license fixed" carry the same words.
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> frozenset[str]:
    words = (w.casefold() for w in _WORD.findall(text))
    return frozenset(_stem(w) for w in words if w not in _STOP)


def uncovered_segments(content: str, derived, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Segments of *content* that none of *derived* holds, at most *limit*."""
    segments = source_segments(content)
    if limit <= 0 or not segments:
        return []
    if len(segments) == 1 and _words(segments[0]) == _words(content):
        return []  # the whole-memory fact already is that sentence
    held = [_words(fact.content) for fact in derived]
    seen: set[str] = set()
    out: list[str] = []
    for segment in segments:
        key = segment.casefold()
        if key in seen or len(segment) < MIN_CHARS or _is_filler(segment):
            continue
        seen.add(key)
        words = _words(segment)
        if not words or any(len(words & h) >= COVERED_SHARE * len(words) for h in held):
            continue
        out.append(segment)
        if len(out) >= limit:
            break
    return out


def coverage_facts(
    content: str,
    derived,
    *,
    session_id: str = "",
    session_date: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[AtomicFact]:
    """A verbatim fact for every segment of *content* that *derived* misses."""
    segments = uncovered_segments(content, derived, limit)
    entity_freq: dict[str, int] = {}
    for segment in segments:
        for entity in _extract_entities(segment):
            entity_freq[entity] = entity_freq.get(entity, 0) + 1
    return [sentence_fact(s, session_id, session_date, entity_freq) for s in segments]
