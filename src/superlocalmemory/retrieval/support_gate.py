# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Does anything in memory support this question? One decision, every signal.

WHY
---
Recall used to decide "answer or abstain" on one signal, the raw cosine at the
evidence floor, and memories made mostly of ids and hashes sit close to every
question in the embedding space, so random text "found" them. The stronger
signals were computed and then thrown away. Here each recalled memory gets one
number -- the probability that it answers the question -- from all of them
(``support_recipe.FEATURES``), and recall answers only when at least one memory
reaches the recipe's threshold.

Per memory, not per question: the evidence that a memory answers has to come
from one memory. "v0.3.4 snazzle" finds the v0.3.4 note by its keyword and a
hub stub by its cosine; neither memory alone supports the whole question.

Deterministic and readable: a weighted sum and a logistic, with every number in
``support_recipe``. Pure: no I/O, no model call. Callers decide what to do with
the verdict (``core/score_contract.py``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from superlocalmemory.retrieval import support_recipe as sr

REASON_UNSUPPORTED = "unsupported"
REASON_NO_RESULTS = "no_results"
#: What a person reads when recall abstains (``support_tiers.NONE_NOTE``).
ABSTAIN_NOTE = "That isn't in SuperLocalMemory."
#: The keyword fallback's bar (``lexical_support``): share of content words.
LEXICAL_MIN_COVERAGE = 0.5


@dataclass(frozen=True)
class Assessment:
    supported: bool
    best: float | None
    threshold: float
    probabilities: tuple[float, ...]
    version: str
    reason: str | None
    #: Inputs that were imputed for at least one memory (a cold model, a store
    #: still filling in its baselines): the verdict is weaker, and says so.
    imputed: tuple[str, ...] = ()
    #: An input the recipe needs is missing for the whole recall: no question
    #: vector or probe profile, no memory with a hub-corrected cosine, or (Latin
    #: script) no cross-encoder score. Recall then shows what retrieval found,
    #: labelled related, and never says "That isn't in SuperLocalMemory."
    degraded: bool = False


#: The reranker status that means the cross-encoder scored this recall.
RERANKER_RAN = "applied"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def imputed(evidence: Mapping[str, Any], query_evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """The recipe inputs that had no measurement for this memory."""
    out = []
    if _num(evidence.get("hub_corrected")) is None:
        out.append("hub_corrected")
    if _num(evidence.get("ce_raw")) is None or not query_evidence.get("latin_script", True):
        out.append("cross_encoder")
    if _num(query_evidence.get("nonsense_similarity")) is None:
        out.append("nonsense_similarity")
    return tuple(out)


def features(evidence: Mapping[str, Any], query_evidence: Mapping[str, Any],
             recipe: sr.SupportRecipe = sr.ACTIVE) -> dict[str, float]:
    """The recipe's inputs for one memory, with documented imputations."""
    hub = _num(evidence.get("hub_corrected"))
    if hub is None:
        cos = _num(evidence.get("cosine"))
        hub = (cos - recipe.hub_missing_baseline if cos is not None
               else recipe.hub_corrected_unmeasured)
    ce = _num(evidence.get("ce_raw"))
    if ce is None or not query_evidence.get("latin_script", True):
        ce = recipe.ce_missing
    lo, hi = recipe.ce_clip
    profile = _num(query_evidence.get("nonsense_similarity"))
    return {
        "hub_corrected": hub,
        "keyword_coverage": _num(evidence.get("keyword_coverage")) or 0.0,
        "cross_encoder": min(hi, max(lo, ce)),
        "log_bm25": math.log1p(max(0.0, _num(evidence.get("bm25_raw")) or 0.0)),
        "entity_hit": _num(evidence.get("entity_hit")) or 0.0,
        "oov_phrase": _num(query_evidence.get("oov_phrase")) or 0.0,
        "rare_miss": _num(evidence.get("rare_miss")) or 0.0,
        "nonsense_similarity": (profile if profile is not None
                                else recipe.nonsense_similarity_missing),
        # None: nothing to judge (no plain-word subject term) or no keyword
        # index; neither is evidence that a word is missing.
        "orphan_terms": _num(query_evidence.get("orphan_terms")) or 0.0,
        # A keyword query (no question word) is answered only by a memory that
        # carries its words; with a question word they may be what is asked.
        "unframed_miss": _unframed_miss(evidence, query_evidence),
    }


def _unframed_miss(evidence: Mapping[str, Any], query_evidence: Mapping[str, Any]) -> float:
    coverage = _num(evidence.get("subject_coverage"))
    if coverage is None or query_evidence.get("question_frame", True):
        return 0.0
    return 1.0 - coverage


def logit(evidence: Mapping[str, Any], query_evidence: Mapping[str, Any],
          recipe: sr.SupportRecipe = sr.ACTIVE, *, verdict: Any = None) -> float:
    f = features(evidence, query_evidence, recipe)
    z = recipe.intercept + sum(w * f[name] for name, w in recipe.weights.items())
    if evidence.get("metadata_hit") and f["rare_miss"] == 0.0:
        z += recipe.metadata_bonus
    if verdict is not None and getattr(verdict, "insufficient", False):
        z += recipe.laya_unsupported
    return z


def probability(evidence: Mapping[str, Any], query_evidence: Mapping[str, Any],
                recipe: sr.SupportRecipe = sr.ACTIVE, *, verdict: Any = None) -> float:
    if not evidence:
        return 0.0  # nothing measured is never support
    z = logit(evidence, query_evidence, recipe, verdict=verdict)
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def _no_cross_encoder(measured: Sequence[Mapping[str, Any]],
                      query_evidence: Mapping[str, Any],
                      reranker_status: str | None = None) -> bool:
    """A Latin-script question the cross-encoder did not score: the reranker
    did not run (loading, failed or not set up). Read from the reranker's own
    status when the caller knows it -- a result outside its window carries no
    score and that is no gap -- else from the scores. Other scripts never use
    it, so its absence there is no gap."""
    if not measured or not query_evidence.get("latin_script", True):
        return False
    if reranker_status:
        return reranker_status != RERANKER_RAN
    return not any(_num(e.get("ce_raw")) is not None for e in measured)


def assess(results: Sequence[Any], query_evidence: Mapping[str, Any], *,
           verdict: Any = None, recipe: sr.SupportRecipe = sr.ACTIVE,
           reranker_status: str | None = None) -> Assessment:
    """The gate's verdict over recall's results (``evidence`` on each)."""
    qe = query_evidence or {}
    probs = tuple(round(probability(getattr(r, "evidence", None) or {}, qe, recipe,
                                    verdict=verdict), 6) for r in results)
    best = max(probs) if probs else None
    supported = best is not None and best >= recipe.threshold
    gaps = sorted({name for r in results if getattr(r, "evidence", None)
                   for name in imputed(r.evidence, qe)})
    reason = None if supported else (REASON_UNSUPPORTED if probs else REASON_NO_RESULTS)
    measured = [r.evidence for r in results if getattr(r, "evidence", None)]
    degraded = (_num(qe.get("nonsense_similarity")) is None
                or not any(_num(e.get("hub_corrected")) is not None for e in measured)
                or _no_cross_encoder(measured, qe, reranker_status))
    return Assessment(supported=supported, best=best, threshold=recipe.threshold,
                      probabilities=probs, version=recipe.version, reason=reason,
                      imputed=tuple(gaps), degraded=degraded)


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def switched_off(retrieval_config: Any) -> bool:
    """The answer-support gate, and everything that came with it (the floor's
    hub-corrected door), is off: ``SLM_RECALL_NO_SUPPORT_GATE`` set to 1, true
    or yes, or ``retrieval.answer_support_gate`` false."""
    import os

    if os.environ.get("SLM_RECALL_NO_SUPPORT_GATE", "").strip().lower() in _TRUTHY:
        return True
    return not bool(getattr(retrieval_config, "answer_support_gate", True))


def lexical_support(query: str, content: str) -> bool:
    """The gate when only words are left (a recall past its budget falls back
    to keyword search, with no vectors and no cross-encoder): the memory must
    carry every rare term of the question and ``LEXICAL_MIN_COVERAGE`` of its
    content words, so "v0.3.4 snazzle" cannot answer with the v0.3.4 note."""
    from superlocalmemory.retrieval import evidence_features as ef

    terms = ef.query_terms(query or "")
    if not terms:
        return False
    rare = ef.rare_terms(terms, ef.model_vocabulary())
    if any(ef.unspaced(t) for t in terms):  # character pairs, not whole terms
        coverage = ef.subject_coverage(terms, content)
    else:
        coverage = ef.keyword_coverage(terms, content)
    return ef.rare_miss(rare, content) == 0.0 and coverage >= LEXICAL_MIN_COVERAGE


__all__ = ["ABSTAIN_NOTE", "Assessment", "LEXICAL_MIN_COVERAGE", "REASON_NO_RESULTS",
           "REASON_UNSUPPORTED", "assess", "features", "imputed", "lexical_support", "logit",
           "probability", "switched_off"]
