# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Whether a lower-case word is an ordinary English word.

People type questions in lower case ("what did alice say", "when is priya's
birthday"), so a lower-case question word must still be able to reach an
entity. It must not when it is an everyday word that some note happened to
capitalise: "lost", "post", "cost" and "plot" are entities on real stores.

The word list is the public-domain 12dicts "3esl" list (provenance and licence
in the header of ``data/common_english_words.txt``). It holds base forms, so a
regular inflection is reduced before the lookup ("wants" -> "want",
"posted" -> "post", "studies" -> "study").
"""

from __future__ import annotations

import threading
from importlib import resources

_LOCK = threading.Lock()
_WORDS: frozenset[str] | None = None

#: (suffix, replacement) tried in order; each candidate base is looked up.
_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"),
    ("ing", ""), ("ing", "e"), ("er", ""), ("er", "e"), ("est", ""), ("ly", ""),
)


def _load() -> frozenset[str]:
    global _WORDS
    with _LOCK:
        if _WORDS is None:
            text = resources.files("superlocalmemory.encoding").joinpath(
                "data", "common_english_words.txt").read_text(encoding="utf-8")
            _WORDS = frozenset(line for line in text.splitlines()
                               if line and not line.startswith("#"))
        return _WORDS


def _bases(word: str) -> list[str]:
    out = [word]
    for suffix, repl in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            stem = word[: -len(suffix)] + repl
            out.append(stem)
            if len(stem) >= 3 and stem[-1] == stem[-2] and not repl:
                out.append(stem[:-1])  # "planned" -> "plann" -> "plan"
    return out


def is_common_word(word: str) -> bool:
    """True for an everyday English word or a regular inflection of one."""
    w = (word or "").strip().lower()
    if not w.isalpha():
        return False
    words = _load()
    return any(base in words for base in _bases(w))


__all__ = ["is_common_word"]
