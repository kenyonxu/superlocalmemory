"""SLM Innovation — Behavioral Pattern Detection.

Detects user query patterns per profile: time-of-day habits,
topic sequences, entity preferences. Used to pre-weight
retrieval channels based on user behavior.

Ported from V2.8 with enhancements. Profile-scoped.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime

from slm_innovation.storage.models import BehavioralPattern

logger = logging.getLogger(__name__)


class BehavioralTracker:
    """Track and detect behavioral patterns in query history.

    Detects:
    - Topic preferences (which topics the user queries most)
    - Entity preferences (which entities appear most in queries)
    - Time-of-day patterns (when the user is most active)
    - Query type distribution (factual vs temporal vs opinion)
    """

    def __init__(self, db) -> None:
        self._db = db

    def record_query(
        self,
        query: str,
        query_type: str,
        entities: list[str],
        profile_id: str,
    ) -> None:
        """Record a query for behavioral analysis."""
        hour = datetime.now(UTC).hour
        pattern_key = f"hour_{hour}"
        self._upsert_pattern("time_of_day", pattern_key, profile_id)

        if query_type:
            self._upsert_pattern("query_type", query_type, profile_id)

        for entity in entities[:5]:  # Top 5 entities per query
            self._upsert_pattern("entity_pref", entity.lower(), profile_id)

    def get_patterns(
        self, pattern_type: str, profile_id: str, min_confidence: float = 0.0
    ) -> list[BehavioralPattern]:
        """Get learned patterns of a specific type."""
        rows = self._db.execute(
            "SELECT * FROM behavioral_patterns "
            "WHERE profile_id = ? AND pattern_type = ? AND confidence >= ? "
            "ORDER BY confidence DESC",
            (profile_id, pattern_type, min_confidence),
        )
        return [self._row_to_pattern(r) for r in rows]

    def get_entity_preferences(self, profile_id: str, top_k: int = 10) -> list[str]:
        """Get most queried entities for a profile."""
        patterns = self.get_patterns("entity_pref", profile_id)
        return [p.pattern_key for p in patterns[:top_k]]

    def get_active_hours(self, profile_id: str) -> list[int]:
        """Get hours when user is most active (top 5)."""
        patterns = self.get_patterns("time_of_day", profile_id)
        hours = []
        for p in patterns[:5]:
            try:
                hours.append(int(p.pattern_key.replace("hour_", "")))
            except ValueError:
                continue
        return hours

    def get_query_type_distribution(self, profile_id: str) -> dict[str, float]:
        """Get distribution of query types."""
        patterns = self.get_patterns("query_type", profile_id)
        total = sum(p.observation_count for p in patterns) or 1
        return {
            p.pattern_key: p.observation_count / total
            for p in patterns
        }

    # -- Internal ----------------------------------------------------------

    def _upsert_pattern(
        self, pattern_type: str, pattern_key: str, profile_id: str
    ) -> None:
        """Update or create a behavioral pattern."""
        rows = self._db.execute(
            "SELECT pattern_id, observation_count FROM behavioral_patterns "
            "WHERE profile_id = ? AND pattern_type = ? AND pattern_key = ?",
            (profile_id, pattern_type, pattern_key),
        )
        now = datetime.now(UTC).isoformat()

        if rows:
            d = dict(rows[0])
            new_count = d["observation_count"] + 1
            confidence = min(1.0, new_count / 100.0)  # Saturates at 100 observations
            self._db.execute(
                "UPDATE behavioral_patterns SET observation_count = ?, "
                "confidence = ?, last_updated = ? WHERE pattern_id = ?",
                (new_count, confidence, now, d["pattern_id"]),
            )
        else:
            pattern = BehavioralPattern(
                profile_id=profile_id,
                pattern_type=pattern_type,
                pattern_key=pattern_key,
                confidence=0.01,
                observation_count=1,
                last_updated=now,
            )
            self._db.execute(
                "INSERT INTO behavioral_patterns "
                "(pattern_id, profile_id, pattern_type, pattern_key, "
                "pattern_value, confidence, observation_count, last_updated) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (pattern.pattern_id, pattern.profile_id, pattern.pattern_type,
                 pattern.pattern_key, "", pattern.confidence,
                 pattern.observation_count, pattern.last_updated),
            )

    @staticmethod
    def _row_to_pattern(row) -> BehavioralPattern:
        d = dict(row)
        return BehavioralPattern(
            pattern_id=d["pattern_id"],
            profile_id=d["profile_id"],
            pattern_type=d["pattern_type"],
            pattern_key=d["pattern_key"],
            pattern_value=d.get("pattern_value", ""),
            confidence=d["confidence"],
            observation_count=d["observation_count"],
            last_updated=d["last_updated"],
        )
