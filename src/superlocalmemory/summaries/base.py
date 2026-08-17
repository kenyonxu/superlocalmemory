# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Base types for the #113 bounded summary layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SummaryResult:
    """A bounded, profile-scoped, traceable summary of user memories.

    Maintainer's binding constraint (issue #113 reply):
        "views must be customizable, profile-scoped, privacy-aware, and
         traceable back to the underlying memories rather than becoming
         opaque generic summaries"

    This dataclass enforces three of those four constraints structurally:

    Traceability
        ``source_fact_ids`` carries the atomic_facts.fact_id for every fact
        that contributed to this summary.  A user can always drill back to
        the raw memories.

    Profile scope
        ``profile_id`` is mandatory; callers must never mix profiles.

    Honesty / non-opaqueness
        ``coverage`` must be set to an accurate value.  See the constants
        below.  A summary over 3.9% of facts that presents itself as "your
        session" is precisely the opaque generic summary the maintainer said
        to avoid.

    Generated-by
        ``generated_by`` records whether the content is extractive
        (deterministic, always available, Mode A default) or came from an
        LLM (Mode B Ollama / Mode C cloud).

    Attributes:
        kind:            "session" | "daily" | "project"
        profile_id:      Owning profile — never expose across profiles.
        content:         Human-readable summary text.
        source_fact_ids: IDs of the atomic_facts that contributed.
                         Empty only when the underlying data does not exist.
        coverage:        One of the COVERAGE_* constants below.
        generated_by:    One of the GENERATED_BY_* constants below.
        metadata:        Extra context: date, project_path, session_id, etc.
    """

    kind: str
    profile_id: str
    content: str
    source_fact_ids: list[str]
    coverage: str
    generated_by: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── coverage constants ──────────────────────────────────────────────────────
#
# Use these strings; the acceptance gate checks for their presence
# and the values must be human-interpretable without this file.

COVERAGE_FULL = "full"
"""All relevant data was available and contributed to the summary."""

COVERAGE_PARTIAL = "partial"
"""Some data was available.  Session summaries are always at most partial
because only ~3.9% of facts carry a session_id on a real store."""

COVERAGE_INSUFFICIENT = "insufficient"
"""Too few facts to produce a meaningful summary (below MIN_FACTS threshold)."""

COVERAGE_NO_SESSION = "no_session"
"""Session ID not found, or the session has no associated facts."""

COVERAGE_UNAVAILABLE = "unavailable"
"""Required data does not exist or a query error prevented access."""


# ── generated_by constants ──────────────────────────────────────────────────
#
# extractive is the deterministic fallback, ALWAYS available.
# Mode A users never get anything else.  Mode B/C users fall back when
# Ollama or the cloud is down — silence is not an option.

GENERATED_BY_EXTRACTIVE = "extractive"
"""Deterministic extractive summary — no LLM.  Always available."""

GENERATED_BY_LLM_B = "llm_b"
"""Ollama local LLM (Mode B).  Falls back to extractive if unavailable."""

GENERATED_BY_LLM_C = "llm_c"
"""Cloud LLM (Mode C).  Falls back via llm_b to extractive."""


# ── highlight formatting ────────────────────────────────────────────────────

#: Display width for one bullet in a summary body.
#:
#: Chosen for a bullet, not for a paragraph. The generators originally truncated
#: at 300 characters and nothing else, which looks fine on a synthetic corpus of
#: one-line facts and falls apart on a real store: agent-written facts routinely
#: contain blank lines and markdown headings, so a 300-character slice rendered
#: as six or more display lines and the bullet list stopped being a list.
HIGHLIGHT_CHARS = 180


def format_highlight(content: str, limit: int = HIGHLIGHT_CHARS) -> str:
    """Collapse *content* to a single readable line for a summary bullet.

    Three things, in order:

    1. **Flatten whitespace.** Newlines, blank lines and runs of spaces all
       become one space. This is the fix that matters: character truncation
       alone cannot keep a multi-paragraph fact on one line, and every
       generator here writes into a bullet list.
    2. **Prefer a whole first sentence** when there is one and it fits. A
       complete sentence reads better than a slice of one, and the first
       sentence of a report is usually its summary.
    3. **Otherwise cut at a word boundary** and mark the cut with an ellipsis,
       so it is visible that text was dropped rather than that a fact ended
       mid-word.

    Markdown heading markers are stripped because a flattened ``**Summary**``
    mid-sentence reads as noise.
    """
    import re

    text = re.sub(r"\s+", " ", (content or "")).strip()
    # Leading/inline markdown emphasis and heading marks, once flattened, add
    # nothing but clutter to a one-line bullet.
    text = re.sub(r"(?:^|\s)#{1,6}\s+", " ", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return ""
    if len(text) <= limit:
        return text

    # A complete first sentence, if it fits comfortably.
    match = re.match(r"(.+?[.!?])(?:\s|$)", text)
    if match:
        sentence = match.group(1).strip()
        if len(sentence) <= limit:
            return sentence

    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:          # don't cut a long unbroken token to a stub
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"


def get_mode_str(config: object | None) -> str:
    """Extract the operating mode string ('a', 'b', or 'c') from a config."""
    if config is None:
        return "a"
    m = getattr(config, "mode", None)
    if m is None:
        return "a"
    return getattr(m, "value", str(m)).lower()
