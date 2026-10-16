# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Rewrite a question so keyword search can find what it means.

WHAT
----
Extra search terms for the keyword (BM25) channel, added to the question:

* simple morphology -- the base form of an inflected word ("failing" ->
  "fail", "handles" -> "handle"), from the shipped English word list;
* entity aliases and canonical names -- a question naming "Tom" also searches
  for "Tomás Rivera" and the entity's other aliases, and the other way round,
  read from this profile's entity tables;
* in Mode B, optionally (``retrieval.query_rewrite_llm``), a few keywords from
  the LOCAL model (Ollama only; never a hosted one), bounded by
  ``retrieval.query_rewrite_timeout_s`` and failing open to the deterministic
  rewrite.

WHY IT CANNOT LET NONSENSE THROUGH
----------------------------------
The rewrite only reaches the keyword channel. The cross-encoder, the evidence
features and the answer-support gate all read the ORIGINAL question, so a
rewrite can bring a candidate into view but can never make it count as
support for a question it does not answer.

Deterministic: the same question and store give the same rewrite.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z]+")
_NAME_TOKENS = re.compile(r"\w+", re.UNICODE)
#: Most terms a rewrite may add, so a long question cannot balloon the search.
MAX_ADDED = 12
_MAX_NGRAM = 3
_LLM_SYSTEM = ("You rewrite a search question into at most six extra search keywords "
               "(synonyms, the formal name of a thing, related nouns). Reply with the "
               "keywords only, separated by spaces. Never answer the question.")


@dataclass(frozen=True)
class Rewrite:
    lexical_query: str
    added: tuple[str, ...]
    source: str  # "deterministic" | "local_model" | "none"


def morphology_terms(query: str) -> list[str]:
    """Base forms of the question's inflected English words."""
    from superlocalmemory.encoding.common_words import _bases, is_common_word

    out: list[str] = []
    for word in _WORD.findall(query.lower()):
        if len(word) < 4:
            continue
        for base in _bases(word)[1:]:
            if base != word and len(base) >= 3 and is_common_word(base) and base not in out:
                out.append(base)
                break
    return out


def _ngrams(query: str) -> list[str]:
    tokens = _NAME_TOKENS.findall(query.lower())
    grams = {" ".join(tokens[i:i + n]) for n in range(1, _MAX_NGRAM + 1)
             for i in range(len(tokens) - n + 1)}
    return sorted(g for g in grams if len(g) >= 2)


def alias_terms(db: Any, profile_id: str, query: str) -> list[str]:
    """The other names (canonical name and aliases) of entities the question names."""
    execute = getattr(db, "execute", None)
    grams = _ngrams(query)
    if not callable(execute) or not grams:
        return []
    marks = ",".join("?" for _ in grams)
    try:
        # Both lookups use the NOCASE indexes (canonical name per profile, alias).
        rows = execute(
            "SELECT entity_id FROM canonical_entities WHERE profile_id = ? "
            f"AND canonical_name COLLATE NOCASE IN ({marks}) "
            "UNION SELECT entity_id FROM entity_aliases "
            f"WHERE alias COLLATE NOCASE IN ({marks}) AND profile_id = ?",
            (profile_id, *grams, *grams, profile_id))
        ids = [dict(r)["entity_id"] for r in rows]
        if not ids:
            return []
        id_marks = ",".join("?" for _ in ids)
        names = execute(
            "SELECT canonical_name AS name FROM canonical_entities "
            f"WHERE entity_id IN ({id_marks}) "
            f"UNION SELECT alias AS name FROM entity_aliases WHERE entity_id IN ({id_marks}) "
            "AND profile_id = ? ORDER BY name", (*ids, *ids, profile_id))
    except Exception as exc:  # an older store without these tables: nothing to add
        logger.debug("query rewrite aliases: %s", exc)
        return []
    named = set(grams)
    return [n for n in (str(dict(r)["name"]) for r in names) if n.lower() not in named]


def _local_model(llm: Any) -> bool:
    available = getattr(llm, "is_available", None)
    ok = available() if callable(available) else bool(available)
    return bool(ok) and getattr(llm, "provider", "") == "ollama"


def llm_terms(query: str, llm: Any, timeout_s: float) -> list[str] | None:
    """Keywords from the local model, or None when it did not answer in time."""
    box: dict[str, Any] = {}

    def ask() -> None:
        try:
            box["text"] = llm.generate(prompt=f"Question: {query}", system=_LLM_SYSTEM,
                                       max_tokens=48, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - fail open, never raise into recall
            box["error"] = exc

    worker = threading.Thread(target=ask, name="slm-query-rewrite", daemon=True)
    worker.start()
    worker.join(max(0.0, float(timeout_s)))
    text = box.get("text")
    if worker.is_alive() or text is None:
        return None
    words = _NAME_TOKENS.findall(str(getattr(text, "text", text)))
    return [w for w in words if len(w) >= 3][:6]


def _setting(config: Any, name: str, default: Any) -> Any:
    return getattr(getattr(config, "retrieval", None), name, default)


def rewrite(query: str, *, mode: Any, db: Any, profile_id: str, llm: Any,
            config: Any) -> Rewrite:
    """The keyword-search form of ``query`` (the question itself, plus terms)."""
    from superlocalmemory.storage.models import Mode

    if not _setting(config, "query_rewrite", True):
        return Rewrite(query, (), "none")
    source = "deterministic"
    added = _dedupe([*morphology_terms(query), *alias_terms(db, profile_id, query)], query)
    if (mode == Mode.B and llm is not None and _setting(config, "query_rewrite_llm", False)
            and _local_model(llm)):
        extra = llm_terms(query, llm, _setting(config, "query_rewrite_timeout_s", 0.8))
        if extra:
            added = _dedupe([*added, *extra], query)
            source = "local_model"
    if not added:
        return Rewrite(query, (), source)
    return Rewrite(f"{query} {' '.join(added)}", tuple(added), source)


def _dedupe(terms: Sequence[str], query: str) -> list[str]:
    have = set(_NAME_TOKENS.findall(query.lower()))
    out: list[str] = []
    for t in terms:
        key = t.lower()
        if key and key not in have and key not in {o.lower() for o in out}:
            out.append(t)
    return out[:MAX_ADDED]


__all__ = ["MAX_ADDED", "Rewrite", "alias_terms", "llm_terms", "morphology_terms", "rewrite"]
