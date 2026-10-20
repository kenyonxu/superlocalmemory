# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The answer-support recipe: coefficients and threshold, versioned, readable.

``retrieval/support_gate.py`` turns each recalled memory's evidence into the
probability that it answers the question:

    p = 1 / (1 + exp(-(intercept + sum(weight[f] * feature[f]))))

and recall answers only when some memory's p reaches ``threshold``. Everything
that decides that lives in this file, as plain numbers.

HOW THE NUMBERS WERE MADE
-------------------------
``scripts/quality/answerability_fit.py`` fits them on the synthetic store of
``scripts/quality/answerability_eval.py`` (a ~2,000-memory store shaped like a
year-old agent memory, unevenly described). The questions are split once,
deterministically, per set and category: the weights are fitted on the
training part, the threshold is chosen on the held-out part, and the held-out
numbers per slice are recorded below. Every weight's sign is fixed in advance
(evidence that a memory answers can only raise p; a missing rare term, a
nonsense-looking question can only lower it), so the model stays readable.

Two numbers are priors, not fits, and say so: ``laya_unsupported`` (the
on-device answer check is not installed where the harness runs) and
``metadata_bonus`` (tags and projects are optional; the gate must hold without
them, and a bonus never applies while a rare term of the question is missing).

A new fit is a new version; the old one stays here so a logged decision can
always be traced to the numbers that made it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

logger = logging.getLogger(__name__)

#: The inputs, in order. ``support_gate.features`` defines each one.
FEATURES: tuple[str, ...] = (
    "hub_corrected",        # cosine minus the memory's hub baseline
    "keyword_coverage",     # share of the question's content words in the memory
    "cross_encoder",        # raw cross-encoder score, Latin script only, clipped
    "log_bm25",             # log(1 + raw BM25)
    "entity_hit",           # 1 when the question names an entity the memory carries
    "oov_phrase",           # 1 when the question is out-of-vocabulary gibberish
    "rare_miss",            # share of the question's rare terms the memory lacks
    "nonsense_similarity",  # question's mean cosine to meaningless-sentence probes
    "orphan_terms",         # share of the question's words NO stored memory contains
    "unframed_miss",        # no question word: share of its words this memory lacks
)


@dataclass(frozen=True)
class SupportRecipe:
    version: str
    weights: Mapping[str, float]
    intercept: float
    #: On the probability scale.
    threshold: float
    ce_clip: tuple[float, float]
    #: Raw cross-encoder score used when it has no vote (not run, not warm,
    #: not Latin script).
    ce_missing: float
    #: Hub baseline assumed for a memory that has none yet: high, so a store
    #: still filling in errs toward abstaining.
    hub_missing_baseline: float
    #: Hub-corrected cosine assumed when there is no vector measurement at all
    #: (a keyword-only recall: the model not loaded, or a memory without a
    #: vector). Chosen on the training split: the largest value at which no
    #: training nonsense question answers on keywords alone, minus 0.10.
    hub_corrected_unmeasured: float
    nonsense_similarity_missing: float
    #: Logit shift when the on-device answer check judged the set insufficient.
    laya_unsupported: float
    #: Logit bonus when the question names a tag or project the memory carries.
    metadata_bonus: float
    fitted_on: str
    held_out: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.weights) - set(FEATURES)
        missing = set(FEATURES) - set(self.weights)
        if unknown or missing:
            raise ValueError(f"recipe {self.version}: unknown {sorted(unknown)}, "
                             f"missing {sorted(missing)}")
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        object.__setattr__(self, "held_out", MappingProxyType(dict(self.held_out)))


#: Fitted 12 October 2026 by scripts/quality/answerability_fit.py on harness
#: corpus d21fd3d53039f6e6 (answerability-corpus-v2), profile A, split
#: answer-support-split-v1: 127 training questions, 78 held out. The threshold
#: is the widest-margin point between the highest held-out nonsense question
#: (logit -1.306) and the lowest held-out real question recall answers
#: today (logit 2.004). Keyword coverage sits at its floor (3.0, see
#: the fit script's MIN_WEIGHTS: it carries exact-keyword questions when the
#: cross-encoder is cold); BM25 and entity hits came out at zero, the rare-term
#: miss carrying their signal ("v0.3.4 snazzle" misses half its rare terms,
#: bare "v0.3.4" none).
ANSWER_SUPPORT_V1 = SupportRecipe(
    version="answer-support-v1",
    weights={"hub_corrected": 16.547, "keyword_coverage": 3.0,
             "cross_encoder": 0.0893, "log_bm25": 0.0, "entity_hit": 0.0,
             "oov_phrase": -1.434, "rare_miss": -4.3724,
             "nonsense_similarity": -31.3056, "orphan_terms": 0.0, "unframed_miss": 0.0},
    intercept=9.0324,
    threshold=0.5864,  # logit 0.3491
    ce_clip=(-12.0, 10.0),
    ce_missing=-6.38,
    hub_missing_baseline=0.59,
    hub_corrected_unmeasured=0.1,  # last safe training value 0.20, minus 0.10
    nonsense_similarity_missing=0.39,
    laya_unsupported=-1.5,  # prior: Laya is not available where the harness runs
    metadata_bonus=0.5,     # prior: tags and projects are optional evidence
    fitted_on="answerability-corpus-v2/d21fd3d53039f6e6/answer-support-split-v1",
    held_out={
        "all": "real kept 39/39, nonsense abstain 24/24, "
               "no-answer abstain 5/12",
        "legacy": "real kept 15/15, nonsense abstain 24/24",
        "described": "real kept 24/24, nonsense abstain 24/24",
    },
)

#: Fitted 12 October 2026 on the development half only: the hand-written sets
#: plus the generated dev half (scripts/quality/answerability_questions.py),
#: 723 questions, split answer-support-split-v1 (437 train, 286 held out). New
#: inputs: orphan_terms and unframed_miss. The generated fresh half was never
#: fitted, never used for the threshold.
#:
#: No threshold keeps every real question recall answers today AND abstains on
#: 99% of development nonsense: one hand-written paraphrase with no shared word
#: ("How did we stop write contention failures?", logit -0.746) sits below
#: 17 of 394 development nonsense questions. This threshold keeps it,
#: less a 0.1 margin (no real question lost); one just below the next real
#: question (logit 1.291) would lose it and let 1 through. That choice is
#: the owner's, not the fit's. Held-out below is at this threshold (train: real
#: 120/120, nonsense 225/238).
ANSWER_SUPPORT_V2 = SupportRecipe(
    version="answer-support-v2",
    weights={"hub_corrected": 12.7341, "keyword_coverage": 3.0,
             "cross_encoder": 0.1324, "log_bm25": 0.0, "entity_hit": 0.0,
             "oov_phrase": 0.0, "rare_miss": -1.6932,
             "nonsense_similarity": -40.3182, "orphan_terms": -4.4236,
             "unframed_miss": -5.4385},
    intercept=13.8629,
    threshold=0.3003,  # logit -0.8460: lowest real -0.7460 less 0.1
    ce_clip=(-12.0, 10.0),
    ce_missing=-9.05,
    hub_missing_baseline=0.594,
    hub_corrected_unmeasured=0.0,
    nonsense_similarity_missing=0.39,
    laya_unsupported=-1.5,  # prior: Laya is not available where the harness runs
    metadata_bonus=0.5,     # prior: tags and projects are optional evidence
    fitted_on="answerability-corpus-v2/d21fd3d53039f6e6/hand+generated-dev/answer-support-split-v1",
    held_out={
        "all": "real kept 77/77, nonsense abstain 152/156, "
               "no-answer abstain 7/50",
        "legacy": "real kept 32/32, nonsense abstain 155/156",
        "described": "real kept 45/45, "
                     "nonsense abstain 152/156",
    },
)

#: Fitted 13 October 2026 by scripts/quality/answerability_fit.py on the same
#: corpus and split as v2, with the store built by EmbeddingGemma 2
#: (answerability_eval.py --embedder gemma), 425 training and 278 held-out
#: questions after dropping 20 measured while the model was still loading.
#: "Answered today" is what retrieval put in the top 5 with the gate off: v2
#: refused all 203 real questions on this model, so recall's own answers could
#: not define it. The two cosine inputs come out far from v2 (hub_corrected
#: 25.3 vs 12.7, nonsense_similarity -4.5 vs -40.3) and so do the imputations
#: (nonsense similarity 0.65 vs 0.39): v2's nonsense term alone cost every
#: Gemma answer about 10 logits. Threshold: lowest held-out real less 0.1.
#:
#: NOT USED. On the frozen half (never fitted, never tuned on) it kept 94/94
#: real questions but refused 69% of nonsense (nomic v2: 98.5%), 0/55 of the
#: "real term plus made-up words" set and 0/94 questions with no answer: it
#: would answer what is not in memory. Recall says so out loud instead (v2 on
#: this model refuses; the card, slm doctor and MCP health point to rollback).
#: Kept so the measurement stays traceable.
ANSWER_SUPPORT_EG2_V1 = SupportRecipe(
    version="answer-support-eg2-v1",
    weights={"hub_corrected": 25.3271, "keyword_coverage": 3.0,
             "cross_encoder": 0.0166, "log_bm25": 0.0, "entity_hit": 0.0,
             "oov_phrase": 0.0, "rare_miss": -2.3851,
             "nonsense_similarity": -4.5149, "orphan_terms": -2.4274,
             "unframed_miss": -8.0912},
    intercept=2.6995,
    threshold=0.1414,  # logit -1.8039: lowest held-out real -1.7039 less 0.1
    ce_clip=(-12.0, 10.0),
    ce_missing=-9.65,
    hub_missing_baseline=0.773,
    hub_corrected_unmeasured=0.0,
    nonsense_similarity_missing=0.65,
    laya_unsupported=-1.5,  # prior, as v2
    metadata_bonus=0.5,     # prior, as v2
    fitted_on="answerability-corpus-v2/d21fd3d53039f6e6/embeddinggemma-2/answer-support-split-v1",
    held_out={
        "all": "real kept 74/74, nonsense abstain 144/156, no-answer abstain 2/46",
        "legacy": "real kept 30/30, nonsense abstain 150/156",
        "described": "real kept 44/44, nonsense abstain 144/156",
        "nomic v2 on the same split": "real kept 78/78, nonsense abstain 151/156",
        "frozen half": "real 94/94, nonsense abstain 0.69 (nomic v2 0.985), "
                       "term_plus_words 0/55, no-answer abstain 0/94",
    },
)

#: Fitted 19 October 2026 by scripts/quality/answerability_fit.py on the
#: Chinese twin of that corpus (scripts/quality/answerability_corpus_cjk.py,
#: corpus version answerability-corpus-cjk-v1, digest 8124ea49a983b9b7, a
#: 2,001-memory store built and measured with nomic like v2), profile A, the
#: same split answer-support-split-v1: 132 training questions, 85 held out.
#: The threshold is the widest-margin point between the highest held-out
#: nonsense question (logit -2.361) and the lowest held-out real question
#: (logit 0.432), ~1.4 logits of room on both sides.
#:
#: WHY a Chinese store needs its own numbers: v2 is fitted on the EN corpus
#: and mis-kills Chinese questions -- a Chinese question whose unique term
#: exactly hits its memory scored p 0.0149 against v2's 0.3003 bar (运维笔记
#: §25, 2026-10-19), twenty times short, so every exact hit abstained. The
#: differences from v2 are the fit's, not a lowered bar: the cross-encoder
#: carries real weight (0.3006 vs 0.1324 -- CJK questions hit candidates that
#: are Latin code and ids, where it votes), ``ce_missing`` imputes at 6.98,
#: the median measured vote, and keyword_coverage sits at its floor 3.0 as in
#: every fit. Priors (clip, Laya, metadata) unchanged from v2.
#:
#: Both this and v2 are fitted on nomic, so this is not in the model-key
#: mapping below: ``auto`` keeps v2 and no other store's recall changes; a
#: store that mostly reads Chinese opts in with
#: ``retrieval.answer_support_recipe = "cjk-v1"`` (``select``).
ANSWER_SUPPORT_CJK_V1 = SupportRecipe(
    version="answer-support-cjk-v1",
    weights={"hub_corrected": 9.1744, "keyword_coverage": 3.0,
             "cross_encoder": 0.3006, "log_bm25": 0.0, "entity_hit": 0.9276,
             "oov_phrase": 0.0, "rare_miss": -4.156,
             "nonsense_similarity": -16.6133, "orphan_terms": -5.4854,
             "unframed_miss": -10.1838},
    intercept=9.1836,
    threshold=0.2760,  # logit -0.9642
    ce_clip=(-12.0, 10.0),
    ce_missing=6.98,
    hub_missing_baseline=0.544,
    hub_corrected_unmeasured=0.1,
    nonsense_similarity_missing=0.37,
    laya_unsupported=-1.5,  # prior, as v2
    metadata_bonus=0.5,     # prior, as v2
    fitted_on="answerability-corpus-cjk-v1/8124ea49a983b9b7/answer-support-split-v1",
    held_out={
        "all": "real kept 30/30, nonsense abstain 26/26, "
               "no-answer abstain 11/14",
        "legacy": "real kept 9/9, nonsense abstain 26/26, "
                  "no-answer abstain 13/14",
        "described": "real kept 21/21, nonsense abstain 26/26, "
                     "no-answer abstain 12/14",
    },
)

_RECIPES = {r.version: r for r in (ANSWER_SUPPORT_V1, ANSWER_SUPPORT_V2,
                                   ANSWER_SUPPORT_CJK_V1, ANSWER_SUPPORT_EG2_V1)}
ACTIVE: SupportRecipe = ANSWER_SUPPORT_V2

#: The text model each recipe was fitted on. Two of its inputs are raw cosines
#: (hub_corrected, nonsense_similarity), and every model has its own cosine
#: scale: v2 on EmbeddingGemma 2 scored a correct memory 0.008 against its 0.30
#: bar on a real store (13 Oct 2026). A model is listed here only after
#: scripts/quality/answerability_fit.py passed on the harness built with it.
_BY_MODEL: dict[str, SupportRecipe] = {
    "nomic-ai/nomic-embed-text-v1.5": ANSWER_SUPPORT_V2,
}

#: Models whose recipe met the bar for carrying a store's text: on the held-out
#: split, every real question kept AND nonsense refused at least as often as
#: nomic does (151/156). Fitted is not approved: EmbeddingGemma 2 keeps every
#: real question on held-out but failed the frozen half (see ANSWER_SUPPORT_EG2_V1).
APPROVED_FOR_TEXT: frozenset[str] = frozenset({"nomic-ai/nomic-embed-text-v1.5"})


def _model_key(model: str) -> str:
    """``org/name::768`` and a bare ``name`` both name the same model."""
    return (model or "").split("::", 1)[0].strip().lower()


def for_model(model: str) -> SupportRecipe | None:
    """The recipe fitted on ``model``; None when no fit exists for it."""
    key = _model_key(model)
    if not key:
        return None
    for name, fitted in _BY_MODEL.items():
        if key in (name, name.rsplit("/", 1)[-1]):
            return fitted
    return None


def recipe(version: str) -> SupportRecipe:
    """The recipe named ``version``; KeyError for one this build does not have."""
    return _RECIPES[version]


#: What ``retrieval.answer_support_recipe`` may name besides "auto": the short
#: name of a recipe an operator can pin. An explicit choice wins over the
#: model-key mapping, so a store that mostly reads Chinese can pin the CJK
#: recipe on a model whose own fit is v2's EN numbers.
_BY_SETTING: dict[str, SupportRecipe] = {
    "v2": ANSWER_SUPPORT_V2,
    "cjk-v1": ANSWER_SUPPORT_CJK_V1,
}


def select(setting: str, model: str = "") -> SupportRecipe:
    """The recipe the configuration asks for, for ``model``. "auto" (and any
    value this build does not know, with a warning) is today's behaviour:
    the recipe fitted on the model, ACTIVE without one; an explicit version
    always wins. Reads nothing but its arguments -- callable whatever else is
    switched off."""
    wanted = (setting or "auto").strip().lower()
    explicit = _BY_SETTING.get(wanted)
    if explicit is not None:
        return explicit
    if wanted != "auto":
        logger.warning("answer_support_recipe %r is not one of auto, %s; "
                       "using auto", setting, ", ".join(_BY_SETTING))
    return for_model(model) or ACTIVE


def approved_for_text(model: str) -> bool:
    """``model``'s recipe met the bar for moving a store's text onto it."""
    key = _model_key(model)
    return bool(key) and any(key in (name, name.rsplit("/", 1)[-1]) for name in APPROVED_FOR_TEXT)


__all__ = ["ACTIVE", "ANSWER_SUPPORT_CJK_V1", "ANSWER_SUPPORT_EG2_V1",
           "ANSWER_SUPPORT_V1", "ANSWER_SUPPORT_V2", "APPROVED_FOR_TEXT", "FEATURES",
           "SupportRecipe", "approved_for_text", "for_model", "recipe", "select"]
