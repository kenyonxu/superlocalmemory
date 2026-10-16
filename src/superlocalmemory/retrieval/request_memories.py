# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""A captured prompt is a request the user made, not a fact about the world.

The prompt auto-capture hook (``/observe``, ``hooks/auto_capture.py``) stores
what the user typed to an agent with ``metadata.source = "auto-capture"``.
Measured on one real store: 45 captured prompts split into about 316 facts in
7 days, and because "What am I working on?" routes as ``recency`` the newest
rows win, so the user's own instructions came back as the answer.

They stay stored and searchable (the user decided to keep them). What changes
is how they rank: they never answer a present-tense question ahead of a memory
an agent or the user saved, and elsewhere they rank below those at equal
relevance. A question that is itself about what the user asked, said or
requested lifts both rules, because then a request is exactly the answer.

The marker is read from the memory a fact derives from, not copied onto each
fact: every fact split out of a prompt inherits it, enrichment cannot
overwrite it (it rewrites ``signal_type`` from the text), and the prompts
already stored are covered without a migration.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: ``metadata.source`` the auto-capture writers stamp on a captured prompt.
REQUEST_SOURCE = "auto-capture"

#: Ranking weight for a request when the question is not about requests. One
#: half is a deliberate, untuned choice: enough to put an agent memory of equal
#: relevance first, not so much that a prompt which is the only thing matching
#: the words disappears from the pool.
REQUEST_WEIGHT = 0.5

#: Present-tense questions have no topical words, so relevance cannot order
#: them; there requests go after every other memory, not merely down a little.
HARD_TIER_QUERY_TYPES = frozenset({"recency"})

_VERBS = (r"(?:asked|asking|ask|told|telling|tell|said|saying|say|requested|requesting"
          r"|request|typed|typing|type|prompted|prompting|instructed|instructing)")
_ABOUT_REQUESTS = re.compile(
    r"\bi(?:'ve|’ve|\s+have|\s+had)?\s+(?:been\s+)?" + _VERBS + r"\b"
    r"|\b(?:my|our)\s+(?:\w+\s+)?(?:requests?|prompts?|instructions?|messages?|questions?|asks?)\b"
    r"|\b(?:requests?|prompts?|asks?|instructions?)\s+(?:did\s+)?(?:i|we)\b"
    r"|\bwhat\s+(?:did|have|had)\s+(?:i|we)\s+" + _VERBS + r"\b",
    re.I,
)


def asks_about_requests(query: str) -> bool:
    """True when the question is about what the user asked, said or requested."""
    return bool(_ABOUT_REQUESTS.search(query or ""))


def demotes_requests(query: str) -> bool:
    """Whether requests should rank below other memories for this question."""
    return not asks_about_requests(query)


def is_request_sql(memory_alias: str) -> str:
    """SQL boolean: the ``memories`` row aliased ``memory_alias`` is a captured prompt.

    ``json_valid`` is inside a CASE because SQLite does not short-circuit AND,
    and ``json_extract`` raises on text that is not JSON.
    """
    column = f"{memory_alias}.metadata_json"
    return (f"(CASE WHEN json_valid({column}) THEN json_extract({column}, '$.source') END)"
            f" = '{REQUEST_SOURCE}'")


def request_fact_ids(db: Any, fact_ids: Iterable[str]) -> frozenset[str]:
    """The subset of ``fact_ids`` that derive from a captured prompt.

    One batched query per recall. A store (or test double) that cannot answer
    returns the empty set after a warning: ranking then falls back to what it
    was, and a recall is never failed over this.
    """
    ids = sorted({i for i in fact_ids if i})
    if not ids:
        return frozenset()
    try:
        marks = ",".join("?" * len(ids))
        rows = db.execute(
            "SELECT af.fact_id AS fact_id FROM atomic_facts af "
            "JOIN memories m ON m.memory_id = af.memory_id "
            f"WHERE af.fact_id IN ({marks}) AND {is_request_sql('m')}",
            tuple(ids),
        )
        return frozenset(str(r["fact_id"]) for r in rows)
    except Exception as exc:  # noqa: BLE001 - ranking must not fail a recall
        logger.warning("Could not tell which results are captured prompts (%s)",
                       type(exc).__name__)
        return frozenset()


def _utility(result: Any) -> float:
    """The final ranking key's value (``recall_pipeline._rank_key``), not its sign."""
    from superlocalmemory.core.recall_pipeline import _rank_key

    return -_rank_key(result)[0]


def _lowered(score: float) -> float:
    """``score`` scaled toward worse by REQUEST_WEIGHT; a negative score sinks, never rises."""
    return score - (1.0 - REQUEST_WEIGHT) * abs(score)


def _tagged(result: Any, rank: int, **changes: Any) -> Any:
    from dataclasses import replace

    chain = list(getattr(result, "evidence_chain", None) or [])
    if "captured_prompt" not in chain:
        chain.append("captured_prompt")
    return replace(result, rank_position=rank, evidence_chain=chain, **changes)


def settle_requests(results: list, db: Any, query: str, query_type: str) -> list:
    """The finished answer with captured prompts placed below the other memories.

    Recall's last ordering step, after learned ranking, continuity and kind
    awareness, which all rewrite the order without knowing a result was a
    prompt. For a present-tense question every prompt goes after every other
    result (relevance cannot order those, see HARD_TIER_QUERY_TYPES); for any
    other question a prompt is ranked as if it scored REQUEST_WEIGHT lower. A
    question about what the user asked changes nothing. New objects; the input
    is not modified.
    """
    if not results or asks_about_requests(query):
        return results
    requests = request_fact_ids(db, (getattr(getattr(r, "fact", None), "fact_id", "")
                                     for r in results))
    if not requests:
        return results
    marked = [r.fact.fact_id in requests for r in results]
    if query_type in HARD_TIER_QUERY_TYPES:
        order = [i for i, m in enumerate(marked) if not m] + [i for i, m in enumerate(marked) if m]
        return [_tagged(results[i], rank) if marked[i] else
                _with_rank(results[i], rank) for rank, i in enumerate(order, start=1)]
    keys = [_lowered(_utility(r)) if m else _utility(r) for r, m in zip(results, marked)]
    order = sorted(range(len(results)), key=lambda i: (-keys[i], results[i].fact.fact_id))
    return [_tagged(results[i], rank, ranking_score=keys[i]) if marked[i] else
            _with_rank(results[i], rank) for rank, i in enumerate(order, start=1)]


def _with_rank(result: Any, rank: int) -> Any:
    from dataclasses import replace

    return result if result.rank_position == rank else replace(result, rank_position=rank)
