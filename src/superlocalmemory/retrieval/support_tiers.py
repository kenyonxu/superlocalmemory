# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Three tiers for a recall: supported, related, none.

``supported``  some memory clears the answer-support gate (``support_gate``).
``related``    none does, but the question is a real one (it does not read as
               nonsense) and a memory has real evidence for it: its cosine
               stands out from its own hub baseline, or it carries every rare
               term and half the question's words. People see up to three of
               them, labelled, with "I don't have this exactly -- here are the
               closest memories."; agents only when they ask (include_related).
``none``       nonsense, or nothing close: "That isn't in SuperLocalMemory."

Pure functions of the evidence recall already computed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from superlocalmemory.retrieval import support_recipe as sr

SUPPORTED = "supported"
RELATED = "related"
NONE = "none"
WEAK = "weak"

NONE_NOTE = "That isn't in SuperLocalMemory."
#: While models, probes or baselines are still loading. "That isn't in
#: SuperLocalMemory." is never said then: nothing was measured to say it.
WARMING_NOTE = ("SuperLocalMemory is still warming up — results may be incomplete. "
                "Try again in a few minutes.")
RELATED_NOTE = "I don't have this exactly — here are the closest memories."
RELATED_HIDDEN_NOTE = ("I don't have this exactly. Closer memories exist; "
                       "ask with include_related to see them.")
#: Most memories a related answer shows, and how much of each.
RELATED_MAX = 3
PREVIEW_CHARS = 240
#: Half or more of the question's subject words in no memory at all: nonsense.
ORPHAN_NONSENSE = 0.5
#: Evidence a related memory needs (see the module docstring).
RELATED_HUB_CORRECTED = 0.10
RELATED_COVERAGE = 0.5


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


def question_is_nonsense(query_evidence: Mapping[str, Any],
                         recipe: sr.SupportRecipe = sr.ACTIVE) -> bool:
    """The question reads as nonsense: gibberish words, half its subject words
    in no memory, or a profile at least as nonsense-like as the median
    nonsense question the recipe was fitted on. Nothing measured is not nonsense."""
    if query_evidence.get("oov_phrase"):
        return True
    orphans = _num(query_evidence.get("orphan_terms"))
    if orphans is not None and orphans >= ORPHAN_NONSENSE:
        return True
    profile = _num(query_evidence.get("nonsense_similarity"))
    return profile is not None and profile >= recipe.nonsense_similarity_missing


def real_evidence(evidence: Mapping[str, Any]) -> bool:
    hub = _num(evidence.get("hub_corrected"))
    if hub is not None and hub >= RELATED_HUB_CORRECTED:
        return True
    coverage = _num(evidence.get("subject_coverage"))
    return (coverage is not None and coverage >= RELATED_COVERAGE
            and (_num(evidence.get("rare_miss")) or 0.0) == 0.0)


def related_indexes(results: Sequence[Any], probabilities: Sequence[float],
                    query_evidence: Mapping[str, Any]) -> list[int]:
    """Indexes of the memories a related answer shows, best first (at most
    ``RELATED_MAX``); empty when the question reads as nonsense."""
    if question_is_nonsense(query_evidence or {}):
        return []
    close = [i for i, r in enumerate(results)
             if real_evidence(getattr(r, "evidence", None) or {})]
    close.sort(key=lambda i: (-probabilities[i], i))
    return close[:RELATED_MAX]


def symbol_only(query: str) -> bool:
    """No letter and no digit at all (emoji, punctuation): nonsense, measured."""
    return bool(query.strip()) and not any(ch.isalnum() for ch in query)


def warming_from(response: Any) -> bool:
    """Signs a recall ran before the models and inputs were ready, read from a
    response that may have no results to assess: the embedding channels were
    still warming or dropped, or the question has no probe profile."""
    from superlocalmemory.retrieval import channel_status as chstat

    if getattr(response, "incomplete_channels", ()):
        return True
    status = (getattr(response, "channel_status", None) or {}).get("semantic")
    if status in (chstat.WARMING, chstat.NO_EMBEDDING):
        return True
    qe = getattr(response, "query_evidence", None) or {}
    return bool(qe) and "nonsense_similarity" in qe and qe["nonsense_similarity"] is None


#: Reranker states that pass: it exists but was not ready, or its call failed.
_RERANKER_LOADING = frozenset({"fallback_not_ready", "error", "timeout"})


def reranker_loading(response: Any) -> bool:
    """The cross-encoder exists but gave no scores this time (still loading,
    or a worker restart): a state that passes, unlike one never set up."""
    return getattr(response, "reranker_status", "") in _RERANKER_LOADING


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


__all__ = ["NONE", "NONE_NOTE", "PREVIEW_CHARS", "RELATED", "RELATED_HIDDEN_NOTE",
           "RELATED_MAX", "RELATED_NOTE", "SUPPORTED", "WARMING_NOTE", "WEAK",
           "preview", "question_is_nonsense", "real_evidence", "related_indexes",
           "reranker_loading", "symbol_only", "warming_from"]
