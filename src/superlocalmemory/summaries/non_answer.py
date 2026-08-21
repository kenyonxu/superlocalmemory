# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Refuse to store a model's non-answer as if it were a memory.

A summarizer is handed a cluster of facts and asked to merge them. When the
cluster has nothing in common the model does not fail — it answers the question
it was asked, in prose, and that prose is indistinguishable from a summary to
any caller that only checks ``if summary:``. On the author's own store the
result was rows reading

    "Unfortunately, there is no information available about 'Gateway', 'State',
     'Bounded', or 'Claude' in the provided text."

sitting at ranks 1, 2 and 3 for "what am I working on".

WHAT THIS IS AND IS NOT
-----------------------
This is a **forward guard**: it stops the next such row from being written. It
is deliberately NOT the repair for rows already stored, because it cannot be.
Measured across the 307 retrieval-eligible consolidated rows on the author's
store, this predicate rejects **34** and lets **273** through — because those
273 are fluent, plausible, entirely generic prose ("The Pro and
SuperLocalMemory (SLM) projects have made significant progress...") that no
honest content predicate can separate from a real summary. Repairing by content
would have cleared a ninth of the problem and declared victory. Existing rows
are handled by provenance instead.

Measured the other way, on 3,894 genuine facts, it rejects 70 — and all 70 are
real defects, not false positives: 68 memories carry raw tool-call markup
scraped in from a transcript, and 2 are a model's refusal that was stored as
though it were a memory ("I cannot verify when ... ended a session. Can I help
you with something else?"). Zero legitimate memories are rejected.

So the bar here is: catch text that is *addressed to the prompt* rather than
*about the facts*, and nothing else. Everything is anchored to the start of the
text or to a whole leading sentence, because a genuine memory may legitimately
contain "there is no" in the middle of a sentence.

Companion to ``clean_llm_summary`` in :mod:`superlocalmemory.summaries.base`,
which strips scaffolding *around* an answer. This one rejects text that is
scaffolding *all the way through*. Run the stripper first: "Here is a concise
summary paragraph: <real content>" is salvageable and must not be discarded.
"""

from __future__ import annotations

import re

__all__ = ["is_non_answer", "NON_ANSWER_PATTERNS", "MIN_USEFUL_CHARS"]


#: A *merged summary* this short did not merge anything. The summarizers already
#: refuse model output under 50 characters; this is the same floor applied to
#: text that arrived by another route (extractive mode, or a pre-computed summary
#: handed in by a caller).
#:
#: It is NOT a floor for memories in general, and ``is_non_answer`` therefore
#: does not apply it unless a caller asks. Measured on the author's store, 730
#: of 3,894 genuine facts are under 50 characters and every sampled one is a
#: real memory — "2026-05-02 is the date when the session ended",
#: "This is the case for keeping AMS." Baking this floor into the default would
#: have made the guard reject a fifth of a user's memory as junk. The floor is
#: the *caller's* policy about its own output, not a fact about text.
MIN_USEFUL_CHARS = 50


#: Each entry is (regex, why-it-is-not-a-memory). The reason travels with the
#: pattern so a future reader can tell whether a new false positive means the
#: pattern is wrong or the input genuinely is a non-answer.
_PATTERN_SOURCES: tuple[tuple[str, str], ...] = (
    (
        r"^\W*(?:unfortunately|regrettably|sadly)\b[^.!?]*\bno\b",
        "opens by apologising for having nothing to say",
    ),
    (
        r"^\W*there\s+(?:is|are)\s+no\s+"
        r"(?:information|mention|reference|facts?|details?|data|content)\b",
        "states the absence of input rather than summarising input",
    ),
    (
        r"\bno\s+(?:information|facts?|details?|data)\s+"
        r"(?:is|are|was|were)?\s*(?:available|provided|given|present)\b",
        "reports an empty input set",
    ),
    (
        r"^\W*(?:i\s+(?:cannot|can't|can\s+not|am\s+unable\s+to)|"
        r"it\s+is\s+not\s+possible\s+to)\b",
        "declines the task",
    ),
    (
        r"^\W*(?:i\s+don'?t|i\s+do\s+not)\s+(?:have|see|find)\b",
        "declines the task in the first person",
    ),
    (
        r"\bthe\s+(?:provided|given|above|following)\s+"
        r"(?:text|facts?|context|input|information)\b",
        "refers to the prompt, so it is talking to the asker, not about the memory",
    ),
    (
        r"^\W*(?:as\s+an?\s+(?:ai|language\s+model)|i'?m\s+an?\s+ai)\b",
        "identifies itself as a model",
    ),
    (
        r"^\W*(?:please\s+)?(?:provide|share|give)\s+(?:me\s+)?"
        r"(?:more|the|some|additional)\b",
        "asks the user for input instead of answering",
    ),
    # The five below were added after running the first four against all 1,035
    # summaries stored on the author's machine. They catch 14 rows the original
    # set let through -- every one a measured string from that store, not a
    # guess about what a model might say.
    (
        r"^\W*i\s+(?:did\s*n[o']?t|didn'?t)\s+receive\b",
        "says it was given nothing to summarise",
    ),
    (
        r"\bthere\s+(?:is|are|was|were)\s+n(?:o|ot)\s+\d+\s+facts?\b",
        "argues with the number of facts it was asked to merge",
    ),
    (
        r"\bthe\s+text\s+(?:snippet\s+)?"
        r"(?:appears|seems|does\s+not|doesn'?t|is\s+not)\b",
        "describes the prompt instead of summarising it",
    ),
    (
        r"\bin\s+the\s+text\s+(?:provided|given|above|supplied)\b"
        r"|\bthe\s+text\s+(?:provided|supplied)\b",
        "refers to the prompt (word order the earlier rule missed)",
    ),
    (
        r"^\W*i\s+must\s+point\s+out\b",
        "opens with meta-commentary about the request",
    ),
    (
        # "This appears to be a detailed log of progress in writing..." — the
        # model describing the shape of what it was shown rather than saying
        # what it says. Six of the first twelve rows the dashboard rendered
        # opened this way. Deliberately narrower than it could be: "This is a
        # summary of an audit session" is clumsy but it does summarise, so it
        # is left alone.
        r"^\W*this\s+(?:appears|seems)\s+to\s+be\b"
        r"|^\W*this\s+text\s+(?:is|appears|seems)\b",
        "describes the shape of the input instead of its content",
    ),
)

#: Compiled once. ``re.IGNORECASE`` throughout — the casing of a refusal is not
#: information. ``re.DOTALL`` is deliberately NOT set: ``[^.!?]*`` and the
#: leading anchors are meant to stay within the opening sentence.
NON_ANSWER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(src, re.IGNORECASE), why) for src, why in _PATTERN_SOURCES
)

#: Tool-call and markup fragments. A memory whose content carries these was
#: assembled from a transcript that still had its plumbing attached; the user
#: sees raw XML in their own memory list. Matched anywhere, not anchored,
#: because the fragment can appear at any offset in a spliced transcript.
_MARKUP = re.compile(
    r"</(?:content|antml:parameter|parameter|invoke|function_calls|thinking)>"
    r"|<(?:antml:)?(?:parameter|invoke|function_calls)\b"
    r"|<\|(?:im_start|im_end|endoftext)\|>",
    re.IGNORECASE,
)


def is_non_answer(text: str | None, *, min_chars: int = 0) -> tuple[bool, str]:
    """Whether ``text`` is a model talking about the prompt, not a memory.

    Returns ``(rejected, reason)``. ``reason`` is empty when the text is
    acceptable, and otherwise names which rule fired — callers log it, so a
    silent rejection never happens and a false positive is diagnosable from
    normal logs rather than needing a repro.

    ``min_chars`` defaults to **no floor**. A caller that knows its own output
    should be long — a summarizer merging three or more facts — passes
    :data:`MIN_USEFUL_CHARS`. See that constant for why this is not the default.

    Cheap and side-effect free: safe to call on every candidate write.

    >>> is_non_answer("Unfortunately, there is no information available.")[0]
    True
    >>> is_non_answer("Varun ships SLM 4.0.10 with an auto-repair migration.")
    (False, '')
    >>> is_non_answer("The team has no information silo left to dismantle.")[0]
    False
    >>> is_non_answer("2026-05-02 is the date the session ended.")
    (False, '')
    >>> is_non_answer("Too short.", min_chars=MIN_USEFUL_CHARS)[0]
    True
    """
    if text is None:
        return True, "empty"
    stripped = text.strip()
    if not stripped:
        return True, "empty"
    if min_chars and len(stripped) < min_chars:
        return True, f"shorter than {min_chars} characters"

    if _MARKUP.search(stripped):
        return True, "contains tool-call or chat-template markup"

    for pattern, why in NON_ANSWER_PATTERNS:
        if pattern.search(stripped):
            return True, why

    return False, ""
