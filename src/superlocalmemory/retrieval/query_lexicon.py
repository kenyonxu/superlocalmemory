# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Whole-word vocabulary coverage is advisory, never a semantic admission rule.

Names, code, products and languages use subwords. A missing whole word cannot
prove gibberish. Answer/evidence checks decide support after retrieval; lexical
and dense retrieval remain available independently of this signal.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_WORD = re.compile(r"[A-Za-z]+")


def vocabulary() -> frozenset[str] | None:
    try:
        from huggingface_hub import try_to_load_from_cache
        path = try_to_load_from_cache(_MODEL, "vocab.txt")
        if not isinstance(path, str):
            return None
        return _read_vocabulary(path)
    except (ImportError, OSError, ValueError):
        return None  # no cache is never a reason to reject a query


def oov_phrase(query: str, vocab: frozenset[str] | None) -> bool:
    words = _WORD.findall(query)
    # Do not guess at single identifiers, numbers or non-Latin languages.
    plain = " ".join(words)
    if len(words) < 3 or not re.fullmatch(r"[A-Za-z\s?!.,'-]+", query):
        return False
    return bool(plain and vocab and not any(word.lower() in vocab for word in words))


def requires_lexical(embedder, query: str) -> bool:
    """Compatibility hook: no vocabulary evidence justifies vetoing dense recall."""
    return False


@lru_cache(maxsize=1)
def _read_vocabulary(path: str) -> frozenset[str]:
    return frozenset(Path(path).read_text(encoding="utf-8").splitlines())
