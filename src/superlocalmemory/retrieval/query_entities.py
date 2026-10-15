# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Entity names in a question, found by the same rules a save uses."""

from __future__ import annotations

import re

_ENTITY_STOP: frozenset[str] = frozenset(
    {
        # Expanded stop list for query entity extraction
        "what",
        "when",
        "where",
        "who",
        "which",
        "how",
        "does",
        "did",
        "the",
        "that",
        "this",
        "there",
        "then",
        "than",
        "they",
        "them",
        "have",
        "has",
        "had",
        "been",
        "being",
        "about",
        "after",
        "before",
        "from",
        "into",
        "with",
        "some",
        "other",
        "would",
        "could",
        "should",
        "will",
        "because",
        "also",
        "just",
        "like",
        "know",
        "think",
        "feel",
        "want",
        "need",
        "make",
        "take",
        "give",
        "tell",
        "said",
        "wow",
        "gonna",
        "got",
        "by",
        "thanks",
        "thank",
        "hey",
        "hi",
        "hello",
        "bye",
        "good",
        "great",
        "nice",
        "cool",
        "right",
        "let",
        "can",
        "might",
        "much",
        "many",
        "more",
        "most",
        "something",
        "anything",
        "everything",
        "nothing",
        "someone",
        "it",
        "my",
        "your",
        "our",
        "their",
        "me",
        "you",
        "we",
        "us",
        "do",
        "if",
        "or",
        "no",
        "to",
        "at",
        "on",
        "in",
        "so",
        "go",
        "come",
        "see",
        "look",
        "say",
        "ask",
        "try",
        "keep",
        "yes",
        "yeah",
        "sure",
        "okay",
        "ok",
        "really",
        "actually",
        "maybe",
        "well",
        "still",
        "even",
        "very",
    }
)


def extract_query_entities(query: str) -> list[str]:
    """Entity names a question writes as names, by the rules a save uses.

    One extractor for questions and saves (encoding/entity_mentions), so a
    question finds a name the way the note that holds it named it. Lower-case
    words are never title-cased into names: "zxqv plorth wimble" used to give
    three candidates, which the spelling match then landed on real entities.
    Lower-case words get an exact-only lookup instead (``query_plain_words``).
    """
    from superlocalmemory.encoding.entity_mentions import extract_mentions

    candidates: list[str] = []
    seen: set[str] = set()
    for name in extract_mentions(query or "", question=True):
        lo = name.lower()
        if lo not in seen and lo not in _ENTITY_STOP and len(name) >= 2:
            seen.add(lo)
            candidates.append(name)
    return candidates


_PLAIN_WORD_RE = re.compile(r"(?<![\w.-])[a-z][a-z0-9]*(?:[-_.:][a-z0-9]+)*(?![\w-])")


def query_plain_words(query: str, limit: int = 24) -> list[str]:
    """Lower-case words and word pairs of a question, for an exact-only lookup
    (EntityResolver.lookup_plain_words): never spelling-matched."""
    from superlocalmemory.encoding.entity_mentions import STOP_WORDS

    words = [w for w in _PLAIN_WORD_RE.findall(query or "")]
    keep = [w if (len(w) >= 3 and w not in STOP_WORDS and w not in _ENTITY_STOP) else ""
            for w in words]
    out: list[str] = []
    for i, word in enumerate(keep):
        if word:
            out.append(word)
            if i + 1 < len(keep) and keep[i + 1]:
                out.append(f"{word} {keep[i + 1]}")
    return list(dict.fromkeys(out))[:limit]


__all__ = ["extract_query_entities", "query_plain_words"]
