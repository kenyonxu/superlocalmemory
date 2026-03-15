# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Entity Graph Channel with Spreading Activation.

SA-RAG pattern: entities from query -> canonical lookup -> graph traversal
with decay. Handles BOTH uppercase and lowercase entity mentions.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: MIT
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superlocalmemory.encoding.entity_resolver import EntityResolver
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{1,}\b")

_ENTITY_STOP: frozenset[str] = frozenset({
    # Expanded stop list for query entity extraction
    "what", "when", "where", "who", "which", "how", "does", "did",
    "the", "that", "this", "there", "then", "than", "they", "them",
    "have", "has", "had", "been", "being", "about", "after", "before",
    "from", "into", "with", "some", "other", "would", "could", "should",
    "will", "because", "also", "just", "like", "know", "think",
    "feel", "want", "need", "make", "take", "give", "tell", "said",
    "wow", "gonna", "got", "by", "thanks", "thank", "hey", "hi",
    "hello", "bye", "good", "great", "nice", "cool", "right",
    "let", "can", "might", "much", "many", "more", "most",
    "something", "anything", "everything", "nothing", "someone",
    "it", "my", "your", "our", "their", "me", "you", "we", "us",
    "do", "if", "or", "no", "to", "at", "on", "in", "so",
    "go", "come", "see", "look", "say", "ask", "try", "keep",
    "yes", "yeah", "sure", "okay", "ok", "really", "actually",
    "maybe", "well", "still", "even", "very",
})


def extract_query_entities(query: str) -> list[str]:
    """Extract entity candidates from query (handles both cases).

    Strategy: find proper nouns in original + title-cased text,
    plus quoted phrases. Deduplicates case-insensitively.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        lo = name.lower()
        if lo not in seen and lo not in _ENTITY_STOP and len(name) >= 2:
            seen.add(lo)
            candidates.append(name)

    for m in _PROPER_NOUN_RE.finditer(query):
        _add(m.group(0))
    for m in _PROPER_NOUN_RE.finditer(query.title()):
        _add(m.group(0))
    for m in re.finditer(r'"([^"]+)"', query):
        _add(m.group(1).strip())

    return candidates


class EntityGraphChannel:
    """Entity-based retrieval with spreading activation (SA-RAG)."""

    def __init__(
        self, db: DatabaseManager,
        entity_resolver: EntityResolver | None = None,
        decay: float = 0.7, activation_threshold: float = 0.1,
        max_hops: int = 3,
    ) -> None:
        self._db = db
        self._resolver = entity_resolver
        self._decay = decay
        self._threshold = activation_threshold
        self._max_hops = max_hops

    def search(self, query: str, profile_id: str, top_k: int = 50) -> list[tuple[str, float]]:
        """Search via entity graph with spreading activation."""
        raw_entities = extract_query_entities(query)
        if not raw_entities:
            return []

        canonical_ids = self._resolve_entities(raw_entities, profile_id)
        if not canonical_ids:
            return []

        # Seed activation from direct entity-linked facts
        activation: dict[str, float] = defaultdict(float)
        visited_entities: set[str] = set(canonical_ids)

        for eid in canonical_ids:
            for fact in self._db.get_facts_by_entity(eid, profile_id):
                activation[fact.fact_id] = max(activation[fact.fact_id], 1.0)

        # Spreading activation through graph edges
        frontier = set(activation.keys())
        for hop in range(1, self._max_hops):
            hop_decay = self._decay ** hop
            if hop_decay < self._threshold:
                break
            next_frontier: set[str] = set()

            for fid in frontier:
                for edge in self._db.get_edges_for_node(fid, profile_id):
                    neighbor = edge.target_id if edge.source_id == fid else edge.source_id
                    propagated = activation[fid] * self._decay
                    if propagated >= self._threshold and propagated > activation.get(neighbor, 0.0):
                        activation[neighbor] = propagated
                        next_frontier.add(neighbor)

            # Discover new entities from activated facts -> get their facts
            new_eids = self._discover_entities(frontier, profile_id, visited_entities)
            for eid in new_eids:
                visited_entities.add(eid)
                for fact in self._db.get_facts_by_entity(eid, profile_id):
                    if hop_decay > activation.get(fact.fact_id, 0.0):
                        activation[fact.fact_id] = hop_decay
                        next_frontier.add(fact.fact_id)

            frontier = next_frontier
            if not frontier:
                break

        results = [(fid, sc) for fid, sc in activation.items() if sc >= self._threshold]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _resolve_entities(self, raw: list[str], profile_id: str) -> list[str]:
        """Resolve raw names to canonical entity IDs."""
        ids: list[str] = []
        seen: set[str] = set()
        if self._resolver is not None:
            for eid in self._resolver.resolve(raw, profile_id).values():
                if eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
        else:
            for name in raw:
                ent = self._db.get_entity_by_name(name, profile_id)
                if ent and ent.entity_id not in seen:
                    seen.add(ent.entity_id)
                    ids.append(ent.entity_id)
        return ids

    def _discover_entities(
        self, fact_ids: set[str], profile_id: str, visited: set[str],
    ) -> list[str]:
        """Find new canonical entity IDs referenced by a set of facts."""
        new: list[str] = []
        seen = set(visited)
        for fid in fact_ids:
            rows = self._db.execute(
                "SELECT canonical_entities_json FROM atomic_facts WHERE fact_id = ?", (fid,),
            )
            if not rows:
                continue
            raw = dict(rows[0]).get("canonical_entities_json")
            if not raw:
                continue
            try:
                for eid in json.loads(raw):
                    if eid not in seen:
                        seen.add(eid)
                        new.append(eid)
            except (ValueError, TypeError):
                continue
        return new
