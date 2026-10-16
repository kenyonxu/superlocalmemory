# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Conservative assertion boundaries for extractive source overrides."""
import re

_UNCERTAIN = re.compile(
    r"\b(not|never|no|none|nothing|cannot|neither|nor|without|unknown|if|unless|may|might|could|would|"
    r"said|says|claims?|whether|reportedly|false|denied|incorrect|suppose|"
    r"draft|rejected|proposal|proposed|example|quoted|read|wrote|pending|undecided)\b", re.I,
)
_QUOTES = re.compile(r'''["“”‘’`]|(?<!\w)'|'(?!\w)''')


def assertion_sentences(memory):
    # A quote can span several sentences. Conservatively leave such documents
    # to the classifier instead of losing the context at a sentence boundary.
    context = r"(draft|rejected|quoted|said|claims?|rumored|imagined|hypothetical|denied|false)"
    # The reader clips at 1800 characters. A source at that limit may have
    # lost its question/quotation terminator, so it cannot prove an assertion.
    if len(memory) >= 1800 or _QUOTES.search(memory) or re.search(context, memory, re.I):
        return ()
    return tuple(
        sentence.rstrip(".! ").strip()
        for sentence in re.findall(r"[^.!?\n]+(?:[.!?]+|$)", memory)
        if "?" not in sentence and not re.search(
            r"\b(draft|rejected|quoted|said|claims?|rumored|imagined|hypothetical)\b",
            sentence, re.I)
    )


def affirmative_sentences(memory):
    if _UNCERTAIN.search(memory) or re.search(r"\b\w+n['’]t\b", memory, re.I):
        return ()  # ambiguity or denial anywhere in the document defeats a source proof
    return tuple(sentence for sentence in assertion_sentences(memory)
                 if not _UNCERTAIN.search(sentence))
