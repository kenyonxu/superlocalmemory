# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Evidence features: what each signal says about one recalled memory.

WHY
---
Whether recall should answer at all used to be decided by one signal, the raw
cosine at the evidence floor (``time_window.has_primary_evidence``). Several
stronger signals were computed and then thrown away before that decision: the
cross-encoder's raw score (normalised within the batch, so the best of several
unrelated memories always read 1.0), how much of the question a memory's words
actually cover (any single BM25 hit counted as full evidence), whether an
entity the question names is one the memory carries, and the nonsense-phrase
check in ``query_lexicon.oov_phrase``, which nothing called.

This module only MEASURES. Each recalled result carries these numbers on
``RetrievalResult.evidence`` and the response carries the question-level ones on
``RecallResponse.query_evidence``; recall trace shows both. Nothing here changes
ranking or whether recall abstains -- that decision is made elsewhere, from
these numbers, once it has been calibrated.

THE FEATURES (per result)
-------------------------
``cosine``            raw cosine between the question and the memory vectors
``hub_baseline``      the memory's mean cosine to its closest fixed probe queries
                      (``retrieval/hub_baseline.py``); None until computed
``hub_corrected``     ``cosine - hub_baseline`` (CSLS style); None without both
``hub_baseline_mean`` / ``hub_corrected_mean``  the same over ALL probes
``ce_raw``            the cross-encoder's own score, before any normalisation
``ce_batch_norm``     the same score min-max normalised in this recall's batch
``bm25_raw``          the BM25 score before the channel's saturating transform
``keyword_coverage``  share of the question's content words found in the memory
``entity_hit``        the question names an entity the memory carries
``subject_coverage``  share of the question's subject words (content words that
                      do not just frame the question) found in the memory; 1.0
                      when the question has none (a non-Latin question)
``question_frame``    (question level) the question has a question word or an
                      auxiliary ("what", "how", "does"); without one, the words
                      ARE the question, as in "ensure_daemon trance"
``orphan_terms``      (question level) share of the question's content words that
                      NO stored memory contains, in any inflection: "release
                      notes for the moon" leaves "moon" an orphan
``rare_miss``         share of the question's rare terms (identifiers, versions,
                      words no dictionary knows) the memory does NOT contain:
                      "v0.3.4 snazzle" against the v0.3.4 note misses half

Question level (``query_evidence``): the nonsense-phrase flag, whether the
question is in Latin script (the cross-encoder is English-only and gets no vote
otherwise), how many rare terms it has, and ``nonsense_similarity`` -- its mean
cosine to the hub probes made of ordinary words with no meaning
(``hub_baseline.query_profile``), so "purple elephants juggling quantum
bananas" reads as what it is.

Pure functions of values recall already has: no I/O, no model call.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

#: Bump when a feature's definition changes; logged features name it.
FEATURES_VERSION = "evidence-features-v2"

# The words a question is made of but that say nothing about its subject.
_QUESTION_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "of", "in", "to", "for",
    "with", "on", "at", "from", "by", "as", "into", "about", "and", "but", "or",
    "not", "so", "if", "then", "than", "that", "this", "these", "those", "it",
    "its", "i", "me", "my", "we", "our", "us", "you", "your", "he", "him", "his",
    "she", "her", "they", "them", "their", "what", "which", "who", "whom",
    "whose", "when", "where", "why", "how", "there", "here", "any", "some",
    "all", "much", "many", "after", "before", "since", "until", "now", "ever",
})
# Unicode words, keeping identifier and version punctuation inside a token
# (ensure_daemon, v0.3.4, asdk_app_6ac9e8, OPS-4471) as one term.
_TERM_RE = re.compile(r"\w+(?:[._:/-]\w+)*", re.UNICODE)
_BM25_SATURATION = 5.0  # bm25_channel._BM25_SATURATION; inverted below
# A term with a digit or an underscore is an identifier, a version or a code.
_IDENTIFIER_RE = re.compile(r"[0-9_]")
_PIECE_RE = re.compile(r"[._:/-]")
#: Pieces shorter than this ("ci", "db", "id") are never judged rare.
_RARE_MIN_LEN = 3
#: Words that frame a question rather than name its subject: never judged as
#: orphans ("How long did the backup take?" asks about the backup).
_FRAME_WORDS = frozenset({
    "long", "need", "needs", "make", "made", "take", "took", "went", "go", "goes",
    "happen", "happened", "use", "used", "uses", "get", "got", "give", "given", "keep",
    "kept", "say", "said", "tell", "know", "way", "thing", "things", "time", "current",
    "currently", "new", "old", "last", "first", "next", "mean", "means", "work", "works",
    "kind", "type", "part", "like", "about", "still", "anything", "something", "details",
})


def query_terms(text: str) -> list[str]:
    """The question's content terms, lowercased, in order, without repeats."""
    seen: dict[str, None] = {}
    for term in _TERM_RE.findall(text.lower()):
        if term not in _QUESTION_WORDS and term not in seen:
            seen[term] = None
    return list(seen)


def _content_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for term in _TERM_RE.findall(text.lower()):
        terms.add(term)
        # "asdk_app_6ac9e8" also contains "asdk", "app" and "6ac9e8"; a question
        # naming any whole piece of an identifier matches that piece.
        terms.update(p for p in re.split(r"[._:/-]", term) if p)
    return terms


def keyword_coverage(terms: Sequence[str], content: str) -> float:
    """Share of ``terms`` present as whole terms in ``content`` (0.0 when none)."""
    if not terms:
        return 0.0
    have = _content_terms(content)
    return sum(1 for t in terms if t in have) / len(terms)


def entity_hit(query: str, entities: Iterable[str]) -> bool:
    """True when the question names, as whole words, an entity the memory carries."""
    lowered = " " + " ".join(_TERM_RE.findall(query.lower())) + " "
    for entity in entities:
        words = _TERM_RE.findall(str(entity).lower())
        if words and len(" ".join(words)) >= 3 and f" {' '.join(words)} " in lowered:
            return True
    return False


def is_latin(text: str) -> bool:
    """True when every letter in ``text`` is Latin script (digits and punctuation
    do not count, so an identifier is Latin). The cross-encoder reads English;
    on Chinese, Hindi or Japanese its score is noise and gets no vote."""
    for ch in text:
        if ch.isalpha() and not (ch.isascii() or "LATIN" in unicodedata.name(ch, "")):
            return False
    return True


#: Everyday words of the other languages people write questions in (German,
#: French, Spanish, Portuguese, Italian, Dutch): never "rare" -- a question
#: does not name a specific thing by asking "wann" or "quando".
_OTHER_LANGUAGE_COMMON = frozenset("""
wann wie was wer wo warum welche welcher welches ist sind die der das den dem des
ein eine einer und oder nicht mit von für auf aus bei nach über unter wird werden
quand comment quoi qui où pourquoi quel quelle est sont les une des et ou pas avec
pour dans sur par cuando como qué quien quién donde dónde por cual cuál los las del
una con para sobre quando como onde porque qual são uma com por sobre não mais
quale dove perché sono gli della delle wanneer hoe waarom welke zijn het een met
voor niet ook naar
""".split())


def _known_word(piece: str, vocab: frozenset[str] | None) -> bool:
    from superlocalmemory.encoding.common_words import is_common_word

    return ((vocab is not None and piece in vocab) or is_common_word(piece)
            or piece in _OTHER_LANGUAGE_COMMON)


def rare_terms(terms: Sequence[str], vocab: frozenset[str] | None) -> list[str]:
    """The question terms that name something specific: identifiers, versions
    and codes (a digit or underscore), and plain-ASCII words that neither the
    shipped English word list nor the model's vocabulary knows ("kestrel",
    "snazzle"). Terms with other letters ("läuft") are not judged."""
    out: list[str] = []
    for term in terms:
        if _IDENTIFIER_RE.search(term):
            out.append(term)
        elif term.isascii() and any(
                len(p) >= _RARE_MIN_LEN and not _known_word(p, vocab)
                for p in _PIECE_RE.split(term) if p):
            out.append(term)
    return out


def rare_miss(rare: Sequence[str], content: str) -> float:
    """Share of ``rare`` the memory does not contain as a whole term (0.0 when
    the question has none). A question that names an identifier is answered
    only by a memory that carries it."""
    if not rare:
        return 0.0
    have = _content_terms(content)
    return sum(1 for t in rare if t not in have) / len(rare)


_FRAME_RE = re.compile(r"[a-z]+")
#: Words that make a string a question rather than a list of keywords.
_QUESTION_FRAME = frozenset({
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "is", "are",
    "was", "were", "do", "does", "did", "can", "could", "should", "will", "would", "has",
    "have", "had",
})


def question_frame(text: str) -> bool:
    """True when the text asks something: a question word or an auxiliary."""
    return bool(set(_FRAME_RE.findall(text.lower())) & _QUESTION_FRAME)


#: Scripts written without spaces between words (Chinese, Japanese, Korean,
#: Thai): the keyword index sees a whole run as one term, so such a term is
#: never judged an orphan.
_UNSPACED = ((0x2E80, 0x9FFF), (0xAC00, 0xD7AF), (0x0E00, 0x0E7F), (0xF900, 0xFAFF))


def _spaced_script(term: str) -> bool:
    return not any(lo <= ord(ch) <= hi for ch in term for lo, hi in _UNSPACED)


#: Most character pairs judged for a question in an unspaced script.
MAX_PAIRS = 6


def unspaced(term: str) -> bool:
    """A term in a script written without spaces (Chinese, Japanese, Thai)."""
    return not _spaced_script(term)


def _pairs(term: str) -> list[str]:
    letters = "".join(ch for ch in term if ch.isalnum())
    seen: dict[str, None] = {}
    for i in range(len(letters) - 1):
        seen.setdefault(letters[i:i + 2], None)
    return list(seen)


#: Most subject words judged per question (each is one keyword-index lookup).
MAX_COUNTABLE = 12


def countable_terms(terms: Sequence[str]) -> list[str]:
    """The question terms judged for ``orphan_share``: terms of three or more
    characters, in a script written with spaces, that name a subject rather
    than frame the question."""
    out: list[str] = []
    for t in terms:
        if not _spaced_script(t):
            # The keyword index sees a whole run as one term, so a run is
            # judged by its character pairs (looked up as substrings).
            out.extend(p for p in _pairs(t)[:MAX_PAIRS] if p not in out)
        elif len(t) >= _RARE_MIN_LEN and t not in _FRAME_WORDS:
            out.append(t)
    return out[:MAX_COUNTABLE]


def orphan_share(countable: Sequence[str], in_store: set[str]) -> float | None:
    """Share of ``countable`` that no stored memory contains; None with none."""
    if not countable:
        return None
    return sum(1 for t in countable if t not in in_store) / len(countable)


def cosine(a: Sequence[float] | None, b: Sequence[float] | None) -> float | None:
    if a is None or b is None or len(a) == 0 or len(a) != len(b):
        return None
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na <= 0.0 or nb <= 0.0:
        return None
    return float(np.dot(va, vb) / (na * nb))


def bm25_raw(unit_score: float | None) -> float | None:
    """Undo the BM25 channel's ``s / (s + k)``: the score FTS5 actually gave."""
    if unit_score is None or unit_score <= 0.0:
        return 0.0 if unit_score is not None else None
    if unit_score >= 1.0:
        return None
    return _BM25_SATURATION * unit_score / (1.0 - unit_score)


def build(
    *,
    query: str,
    terms: Sequence[str],
    query_vector: Sequence[float] | None,
    fact: Any,
    channel_scores: Mapping[str, float] | None,
    ce_raw: float | None = None,
    ce_batch_norm: float | None = None,
    hub_baseline: float | None = None,
    hub_baseline_mean: float | None = None,
    hub_baseline_source: str | None = None,
    rare: Sequence[str] = (),
) -> dict:
    """Every evidence feature for one result, as plain JSON-safe values."""
    cs = channel_scores or {}
    cos = cosine(query_vector, getattr(fact, "embedding", None))
    if cos is None and isinstance(cs.get("semantic"), (int, float)):
        cos = 2.0 * float(cs["semantic"]) - 1.0  # the channel's (cosine + 1) / 2
    entities = [*(getattr(fact, "canonical_entities", None) or ()),
                *(getattr(fact, "entities", None) or ())]
    raw_bm25 = bm25_raw(cs.get("bm25")) if "bm25" in cs else 0.0
    content = getattr(fact, "content", "") or ""
    return {
        "cosine": _r(cos),
        "hub_baseline": _r(hub_baseline),
        # Where the baseline came from: "stored" (the live space's row),
        # "measured" (from the stored vector, on the spot) or "missing". A
        # missing one is what the gate imputes (support_recipe
        # hub_missing_baseline, a constant fitted on one model), so a caller
        # reading the evidence can see which memories were judged on it.
        "hub_baseline_source": hub_baseline_source or (
            "missing" if hub_baseline is None else "stored"),
        "hub_corrected": _corrected(cos, hub_baseline),
        "hub_baseline_mean": _r(hub_baseline_mean),
        "hub_corrected_mean": _corrected(cos, hub_baseline_mean),
        "ce_raw": _r(ce_raw),
        "ce_batch_norm": _r(ce_batch_norm),
        "bm25_raw": _r(raw_bm25),
        "keyword_coverage": _r(keyword_coverage(terms, content)),
        "entity_hit": entity_hit(query, entities),
        "rare_miss": _r(rare_miss(rare, content)),
        "subject_coverage": _r(_subject_coverage(terms, content)),
    }


def subject_coverage(terms: Sequence[str], content: str) -> float:
    """Share of the question's subject words (character pairs in an unspaced
    script) this content holds; 1.0 when the question has none."""
    return _subject_coverage(terms, content)


def _subject_coverage(terms: Sequence[str], content: str) -> float:
    subject = countable_terms(terms)
    if not subject:
        return 1.0
    pairs = [t for t in subject if unspaced(t)]
    words = [t for t in subject if not unspaced(t)]
    have = sum(1 for p in pairs if p in content)
    if words:
        have += keyword_coverage(words, content) * len(words)
    return have / len(subject)


def model_vocabulary() -> frozenset[str] | None:
    """The embedding model's word list when it is cached; None otherwise."""
    from superlocalmemory.retrieval.query_lexicon import vocabulary

    try:
        return vocabulary()
    except Exception:  # never let a cache read break recall
        return None


def query_evidence(query: str, terms: Sequence[str] | None = None, *,
                   rare: Sequence[str] | None = None,
                   nonsense_similarity: float | None = None,
                   orphan_terms: float | None = None) -> dict:
    """Question-level evidence (see the module docstring)."""
    from superlocalmemory.retrieval.query_lexicon import oov_phrase

    vocab = model_vocabulary()
    terms = query_terms(query) if terms is None else terms
    rare = rare_terms(terms, vocab) if rare is None else rare
    return {
        "features_version": FEATURES_VERSION,
        "oov_phrase": bool(oov_phrase(query, vocab)),
        "vocabulary_available": vocab is not None,
        "query_terms": len(terms),
        "rare_terms": len(rare),
        "latin_script": is_latin(query),
        "nonsense_similarity": _r(nonsense_similarity),
        "orphan_terms": _r(orphan_terms),
        "question_frame": question_frame(query),
    }


def _corrected(cos: float | None, baseline: float | None) -> float | None:
    return None if cos is None or baseline is None else _r(cos - baseline)


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)
