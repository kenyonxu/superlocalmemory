# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — 2-Round Sufficiency Verification (EverMemOS Pattern).

Round 1: Standard retrieval → sufficiency check.
Round 2 (if insufficient): LLM generates refined queries → merge → rerank.

Design decisions:
- 2 rounds MAX (3-round decomposition BROKE relational context in S16)
- Trigger: max_score < 0.6 OR multi_hop query type
- Skip agentic entirely for temporal queries (S15 lesson)
- Mode A: heuristic alias expansion (no LLM)
- Mode C: LLM sufficiency judgment with 3-way classification

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: MIT
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from superlocalmemory.storage.models import AtomicFact

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 2
_SUFFICIENCY_SCORE_THRESHOLD = 0.6
_SKIP_TYPES = frozenset()  # Hotpatch: enable agentic for ALL query types including multi_hop

_SUFFICIENCY_SYSTEM = (
    "You evaluate whether retrieved context is sufficient to answer a query. "
    'Respond ONLY with JSON: {"is_sufficient": true/false, "missing_information": "..."}'
)

_REWRITE_SYSTEM = (
    "You rewrite queries for a memory retrieval system. "
    "Respond ONLY with a JSON array of 1-3 rewritten queries: "
    '["query1", "query2"]'
)


class LLMBackend(Protocol):
    """Minimal LLM interface."""
    @property
    def is_available(self) -> bool: ...
    def generate(self, prompt: str, system: str = "",
                 max_tokens: int = 512, temperature: float = 0.0) -> Any: ...


class RetrievalEngine(Protocol):
    """Minimal retrieval engine interface."""
    def recall_facts(self, query: str, profile_id: str,
                     top_k: int, skip_agentic: bool = True,
                     ) -> list[tuple[AtomicFact, float]]: ...


class DatabaseProtocol(Protocol):
    """Minimal DB interface for alias expansion."""
    def get_entity_by_name(self, name: str, profile_id: str) -> Any: ...
    def get_aliases_for_entity(self, entity_id: str) -> list[Any]: ...


@dataclass
class RetrievalRound:
    """Metadata for one retrieval round."""
    round_num: int
    query: str
    result_count: int
    avg_score: float
    is_sufficient: bool


class AgenticRetriever:
    """2-round sufficiency verification (EverMemOS pattern).

    Round 1: Retrieve → check sufficiency.
    Round 2: If insufficient, LLM refines queries → merge → rerank.

    Mode A (no LLM): heuristic alias expansion for round 2.
    Mode C (LLM): full sufficiency check + query refinement.
    """

    def __init__(
        self,
        confidence_threshold: float = _SUFFICIENCY_SCORE_THRESHOLD,
        min_results_ratio: float = 0.5,
        db: DatabaseProtocol | None = None,
    ) -> None:
        self._threshold = confidence_threshold
        self._min_ratio = min_results_ratio
        self._db = db
        self.rounds: list[RetrievalRound] = []

    def retrieve(
        self, query: str, profile_id: str,
        retrieval_engine: RetrievalEngine,
        llm: LLMBackend | None = None,
        top_k: int = 20, query_type: str = "",
    ) -> list[AtomicFact]:
        """2-round retrieval with sufficiency check."""
        self.rounds = []

        # S15: skip agentic for temporal (but NOT multi_hop — bridge handles that)
        if query_type in _SKIP_TYPES:
            logger.debug("Skipping agentic for query_type=%s", query_type)
            return [f for f, _ in retrieval_engine.recall_facts(
                query, profile_id, top_k=top_k, skip_agentic=True)]

        # Round 1: standard retrieval
        r1 = retrieval_engine.recall_facts(
            query, profile_id, top_k=top_k, skip_agentic=True,
        )
        r1_avg = _avg(r1)
        max_score = max((s for _, s in r1), default=0.0)

        # Sufficiency check
        is_sufficient = self._check_sufficiency(query, r1, llm)
        self.rounds.append(RetrievalRound(1, query, len(r1), r1_avg, is_sufficient))

        # Return if sufficient OR no way to improve (no LLM and no DB)
        if is_sufficient:
            return [f for f, _ in r1[:top_k]]

        # Trigger round 2 only when: low score OR multi_hop
        needs_round2 = (
            max_score < self._threshold
            or query_type == "multi_hop"
            or len(r1) < 3
        )
        if not needs_round2:
            return [f for f, _ in r1[:top_k]]

        # Round 2: refinement
        pool: dict[str, tuple[AtomicFact, float]] = {
            f.fact_id: (f, s) for f, s in r1
        }

        if llm is not None and getattr(llm, "is_available", False):
            # Mode C: LLM generates refined queries
            refined = self._llm_refine(query, r1, llm)
        else:
            # Mode A: heuristic alias expansion
            refined = self._heuristic_expand(query, profile_id)

        for rq in refined:
            rn = retrieval_engine.recall_facts(
                rq, profile_id, top_k=top_k, skip_agentic=True,
            )
            for fact, score in rn:
                existing = pool.get(fact.fact_id)
                if existing is None or score > existing[1]:
                    pool[fact.fact_id] = (fact, score)
            self.rounds.append(
                RetrievalRound(2, rq, len(rn), _avg(rn), True),
            )

        merged = sorted(pool.values(), key=lambda x: x[1], reverse=True)
        return [f for f, _ in merged[:top_k]]

    # -- Sufficiency check ---------------------------------------------------

    def _check_sufficiency(
        self, query: str, results: list[tuple[AtomicFact, float]],
        llm: LLMBackend | None,
    ) -> bool:
        """Three-way sufficiency: SUFFICIENT / INSUFFICIENT / AMBIGUOUS."""
        if not results:
            return False

        max_score = max((s for _, s in results), default=0.0)

        # Heuristic fast path: clearly sufficient
        if max_score >= 0.8 and len(results) >= 5:
            return True

        # Heuristic fast path: clearly insufficient
        if max_score < 0.3 or len(results) < 2:
            return False

        # LLM sufficiency check (Mode C only)
        if llm is not None and getattr(llm, "is_available", False):
            try:
                top5_context = "\n".join(
                    f"- {f.content}" for f, _ in results[:5]
                )
                prompt = (
                    f"Query: {query}\n\n"
                    f"Retrieved context:\n{top5_context}\n\n"
                    "Is this context sufficient to answer the query?"
                )
                resp = llm.generate(
                    prompt=prompt, system=_SUFFICIENCY_SYSTEM,
                    max_tokens=128, temperature=0.0,
                )
                text = getattr(resp, "text", str(resp))
                parsed = _parse_sufficiency(text)
                if parsed is not None:
                    return parsed
            except Exception as exc:
                logger.warning("Sufficiency check failed: %s", exc)

        # Default: sufficient if score is above threshold
        return max_score >= self._threshold

    # -- Query refinement ----------------------------------------------------

    @staticmethod
    def _llm_refine(
        query: str,
        prev: list[tuple[AtomicFact, float]],
        llm: LLMBackend,
    ) -> list[str]:
        """LLM generates 2-3 refined queries from missing information."""
        ctx = ""
        if prev:
            ctx = f"\nCurrent results: {[f.content[:80] for f, _ in prev[:3]]}"
        try:
            resp = llm.generate(
                prompt=(
                    f"Original query: {query}\n"
                    f"Insufficient results.{ctx}\n"
                    "Generate 2-3 refined search queries to find missing information."
                ),
                system=_REWRITE_SYSTEM,
                max_tokens=256,
                temperature=0.0,
            )
            parsed = _parse_json_strings(getattr(resp, "text", str(resp)))
            if parsed:
                return parsed[:3]
        except Exception as exc:
            logger.warning("LLM refine failed: %s", exc)
        return []

    def _heuristic_expand(
        self, query: str, profile_id: str,
    ) -> list[str]:
        """Mode A: expand query with entity aliases (no LLM)."""
        if self._db is None:
            return []

        expanded_parts: list[str] = []
        entities = re.findall(r"\b[A-Z][a-z]{2,}\b", query)
        for name in entities:
            entity = self._db.get_entity_by_name(name, profile_id)
            if entity:
                aliases = self._db.get_aliases_for_entity(entity.entity_id)
                for a in aliases[:3]:
                    expanded_parts.append(a.alias)

        if expanded_parts:
            return [query + " " + " ".join(expanded_parts)]
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg(results: list[tuple[AtomicFact, float]]) -> float:
    return sum(s for _, s in results) / len(results) if results else 0.0


def _parse_json_strings(raw: str) -> list[str]:
    """Extract JSON string array from LLM output."""
    try:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        items = json.loads(m.group())
        if isinstance(items, list):
            return [str(q).strip() for q in items[:3] if q]
        return []
    except (json.JSONDecodeError, ValueError):
        return []


def _parse_sufficiency(raw: str) -> bool | None:
    """Parse LLM sufficiency response JSON."""
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group())
        if isinstance(data, dict) and "is_sufficient" in data:
            return bool(data["is_sufficient"])
        return None
    except (json.JSONDecodeError, ValueError):
        return None
