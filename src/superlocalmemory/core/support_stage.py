# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The recall pipeline's answer-support stage (``retrieval/support_gate.py``).

Runs after the answer check and before the score contract, on the results the
caller will see. It makes sure every result carries its evidence features (a
result rebuilt after retrieval -- the agentic round, for one -- has none, and a
memory with no evidence can never count as support), marks the memories whose
tag or project the question names (optional bonus evidence), and asks the
gate. ``core/score_contract.py`` applies the verdict.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from superlocalmemory.retrieval import support_gate

logger = logging.getLogger(__name__)

_WORD = re.compile(r"\w+(?:[._:/-]\w+)*", re.UNICODE)
#: Below this many facts in the profile, a word no memory contains says little
#: (most words are in no memory yet), so the orphan-word and rare-term
#: penalties are off; the evidence floor still keeps nonsense out of a small
#: store, and "postgres version" must answer on one.
SMALL_STORE_FACTS = 200
_SIZE_CACHE: dict = {}
_SIZE_TTL_S = 60.0


def enabled(config: Any) -> bool:
    """The gate is on unless switched off in config or by the env kill-switch."""
    return not support_gate.switched_off(getattr(config, "retrieval", None))


def explicit_filter(facets: Any, window: Any, *, as_of: Any = None,
                    known_as_of: Any = None, valid_at: Any = None) -> bool:
    """The caller narrowed recall on purpose: its results keep today's behaviour.
    ``prefer_project`` only reorders, so it is not a filter."""
    if facets is not None and getattr(facets, "narrows", False):
        return True
    return any(v not in (None, "") for v in (window, as_of, known_as_of, valid_at))


def assess(retrieval_engine: Any, query: str, response: Any, verdict: Any,
           profile_id: str, *, metadata: Callable[[list[str]], Mapping] | None = None,
           config: Any = None,
           ) -> support_gate.Assessment | None:
    """Give every result its evidence, then the gate's verdict over them.

    ``config`` is the caller's config (the one whose ``retrieval`` section
    ``enabled`` reads): its ``answer_support_recipe`` picks the gate's recipe.

    None -- no verdict, the score contract unchanged -- when there is nothing
    to judge: no results (the empty set keeps its own reason, since a channel
    may have been down), or an engine that measures no evidence at all (a
    stand-in; the real engine always does, and a failure there is logged).
    """
    results = list(response.results or ())
    if not results:
        return None
    missing = [r for r in results if not getattr(r, "evidence", None)]
    if missing or not response.query_evidence:
        attach = getattr(retrieval_engine, "_attach_evidence", None)
        if callable(attach):
            try:
                q_evidence = attach(query, missing, {}, profile_id)
                if not response.query_evidence:
                    response.query_evidence = q_evidence
            except Exception as exc:  # fail toward abstaining, never raise
                logger.debug("support evidence: %s", exc)
    if not any(getattr(r, "evidence", None) for r in results):
        if hasattr(retrieval_engine, "_attach_evidence"):
            logger.warning("Answer support not measured: no evidence for any result")
        return None
    if _small_store(getattr(retrieval_engine, "_db", None), profile_id):
        _forgive_missing_words(response, results)
    if metadata is not None:
        mark_metadata(results, query, metadata)
    # The local cross-encoder's own status (the online check may have replaced
    # the order afterwards: then it is kept in local_reranker_status).
    status = (getattr(response, "local_reranker_status", "")
              or getattr(response, "reranker_status", None))
    return support_gate.assess(results, response.query_evidence or {}, verdict=verdict,
                               recipe=recipe_for(retrieval_engine, config),
                               reranker_status=status)


def recipe_for(retrieval_engine: Any, config: Any = None):
    """The recipe the gate decides with. ``config`` is the caller's config:
    its ``retrieval.answer_support_recipe`` ("auto", "v2", "cjk-v1") wins --
    an explicit choice overrides the model key. Without one, the recipe
    fitted on the live text model; today's recipe for any model without its
    own fit, so no other model's recall changes."""
    from superlocalmemory.retrieval import support_recipe

    setting = getattr(getattr(config, "retrieval", config), "answer_support_recipe", "auto")
    model = getattr(getattr(retrieval_engine, "_embedder", None), "model_name", "") or ""
    return support_recipe.select(str(setting), str(model))


def _small_store(db: Any, profile_id: str) -> bool:
    """Fewer than ``SMALL_STORE_FACTS`` facts in the profile (read once a minute)."""
    import time

    if db is None or not callable(getattr(db, "execute", None)):
        return False
    key, now = (id(db), profile_id), time.monotonic()
    cached = _SIZE_CACHE.get(key)
    if cached is None or now - cached[1] > _SIZE_TTL_S:
        try:
            rows = db.execute("SELECT COUNT(*) AS n FROM atomic_facts WHERE profile_id = ?",
                              (profile_id,))
            count = int(dict(rows[0])["n"]) if rows else 0
        except Exception:  # unreadable: treat as large (the measured default)
            count = SMALL_STORE_FACTS
        cached = (count, now)
        _SIZE_CACHE[key] = cached
    return cached[0] < SMALL_STORE_FACTS


def _forgive_missing_words(response: Any, results: Sequence[Any]) -> None:
    response.query_evidence = {**(response.query_evidence or {}), "orphan_terms": None,
                               "small_store": True}
    for r in results:
        if isinstance(getattr(r, "evidence", None), dict):
            r.evidence["rare_miss"] = 0.0


def mark_metadata(results: Sequence[Any], query: str,
                  metadata: Callable[[list[str]], Mapping]) -> None:
    """``evidence["metadata_hit"]``: the question names a tag or the project of
    the memory. Fail-open; a store without tags simply has no hits."""
    from superlocalmemory.core.project_identity import project_key
    from superlocalmemory.core.tag_identity import tag_key, tag_keys

    words = _WORD.findall(query.lower())
    wanted = {k for w in words if (k := tag_key(w))} | {
        k for w in words if (k := project_key(w))}
    try:
        rows = metadata([r.fact.fact_id for r in results])
    except Exception as exc:
        logger.debug("support metadata: %s", exc)
        return
    for r in results:
        row = rows.get(r.fact.fact_id) or {}
        have = set(tag_keys(row.get("tags")))
        project = project_key(row.get("project")) if row.get("project") else None
        if project:
            have.add(project)
        if isinstance(getattr(r, "evidence", None), dict):
            r.evidence["metadata_hit"] = bool(wanted & have)


def metadata_reader(db: Any) -> Callable[[list[str]], dict[str, dict]]:
    """Tags and project of each fact's memory, one query for the result set."""
    def read(fact_ids: list[str]) -> dict[str, dict]:
        if not fact_ids:
            return {}
        marks = ",".join("?" for _ in fact_ids)
        rows = db.execute(
            "SELECT f.fact_id AS fact_id, json_extract(m.metadata_json, '$.tags') AS tags, "
            "json_extract(m.metadata_json, '$.project') AS project "
            "FROM atomic_facts f JOIN memories m ON m.memory_id = f.memory_id "
            f"WHERE f.fact_id IN ({marks})", tuple(fact_ids))
        return {str(dict(r)["fact_id"]): dict(r) for r in rows}
    return read


__all__ = ["assess", "enabled", "explicit_filter", "mark_metadata", "metadata_reader"]
