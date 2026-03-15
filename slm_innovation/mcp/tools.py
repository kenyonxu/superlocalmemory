"""MCP tool definitions for SuperLocalMemory V3.

Progressive disclosure pattern:
  Layer 1: search() → compact results (50-100 tokens per result)
  Layer 2: timeline() → chronological view with dates
  Layer 3: get_details() → full fact content by ID

This design reduces token usage by 10x for typical queries.
Most queries only need Layer 1 results.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slm_innovation.core.engine import MemoryEngine
from slm_innovation.storage.models import Mode


@dataclass
class CompactResult:
    """Layer 1: compact search result (~50 tokens)."""

    fact_id: str
    topic: str
    score: float
    category: str
    date: str | None


@dataclass
class TimelineEntry:
    """Layer 2: chronological entry (~80 tokens)."""

    fact_id: str
    content_preview: str
    date: str | None
    speaker: str
    session_id: str


@dataclass
class FullDetail:
    """Layer 3: complete fact content (~200+ tokens)."""

    fact_id: str
    content: str
    fact_type: str
    entities: list[str]
    confidence: float
    importance: float
    observation_date: str | None
    referenced_date: str | None
    channel_scores: dict[str, float]


class MCPToolHandler:
    """Handles MCP tool calls with progressive disclosure."""

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine

    def search(
        self,
        query: str,
        profile_id: str | None = None,
        limit: int = 10,
    ) -> list[CompactResult]:
        """Layer 1: compact search results.

        Returns topic summary + score + category for each result.
        Token-efficient for most queries.
        """
        pid = profile_id or self._engine.profile_id
        response = self._engine.recall(query, pid, limit=limit)

        results: list[CompactResult] = []
        for r in response.results:
            topic = r.fact.content[:60].strip()
            if len(r.fact.content) > 60:
                topic += "..."
            results.append(CompactResult(
                fact_id=r.fact.fact_id,
                topic=topic,
                score=round(r.score, 4),
                category=r.fact.fact_type.value,
                date=r.fact.observation_date,
            ))
        return results

    def timeline(
        self,
        query: str,
        profile_id: str | None = None,
        limit: int = 20,
    ) -> list[TimelineEntry]:
        """Layer 2: chronological view with date context.

        Returns facts sorted by date with speaker and session info.
        """
        pid = profile_id or self._engine.profile_id
        response = self._engine.recall(query, pid, limit=limit)

        entries: list[TimelineEntry] = []
        for r in response.results:
            preview = r.fact.content[:120].strip()
            if len(r.fact.content) > 120:
                preview += "..."

            # Determine speaker from content prefix
            speaker = ""
            content = r.fact.content
            if content.startswith("[") and "]:" in content:
                speaker = content[1:content.index("]:")]

            entries.append(TimelineEntry(
                fact_id=r.fact.fact_id,
                content_preview=preview,
                date=r.fact.observation_date or r.fact.referenced_date,
                speaker=speaker,
                session_id=r.fact.session_id or "",
            ))

        # Sort by date
        entries.sort(
            key=lambda e: e.date or "",
            reverse=True,
        )
        return entries

    def get_details(
        self,
        fact_ids: list[str],
        profile_id: str | None = None,
    ) -> list[FullDetail]:
        """Layer 3: full fact content by ID.

        Use after search() or timeline() to get complete details
        for specific facts of interest.
        """
        pid = profile_id or self._engine.profile_id
        self._engine._ensure_init()

        details: list[FullDetail] = []
        for fid in fact_ids:
            fact = self._engine._db.get_fact(fid)
            if fact is None or fact.profile_id != pid:
                continue
            details.append(FullDetail(
                fact_id=fact.fact_id,
                content=fact.content,
                fact_type=fact.fact_type.value,
                entities=fact.canonical_entities,
                confidence=fact.confidence,
                importance=fact.importance,
                observation_date=fact.observation_date,
                referenced_date=fact.referenced_date,
                channel_scores={},
            ))
        return details

    def remember(
        self,
        content: str,
        profile_id: str | None = None,
        session_id: str = "",
        tags: str = "",
    ) -> dict[str, Any]:
        """Store a memory. Returns fact IDs."""
        pid = profile_id or self._engine.profile_id
        fact_ids = self._engine.store(
            content=content,
            session_id=session_id,
        )
        return {
            "success": True,
            "fact_ids": fact_ids,
            "count": len(fact_ids),
        }

    def forget(
        self,
        fact_id: str,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete a specific fact (right to erasure)."""
        pid = profile_id or self._engine.profile_id
        self._engine._ensure_init()
        try:
            self._engine._db.delete_fact(fact_id)
            return {"success": True, "deleted": fact_id}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
