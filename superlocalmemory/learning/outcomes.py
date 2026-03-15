# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Outcome Tracking & Feedback Loop.

Tracks whether retrieved facts actually helped the user.
Feeds back into adaptive learning and trust scoring.

V1 had OutcomeTracker as a phantom object (instantiated, never used).
Innovation wires outcomes into the learning loop.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from superlocalmemory.storage.models import ActionOutcome

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Track retrieval outcomes and feed into learning.

    When a user acts on retrieved facts (uses them, ignores them,
    corrects them), this creates a feedback signal that improves
    future retrieval quality.

    The feedback loop:
    recall() → user action → record_outcome() → adaptive_learner.train()
    """

    def __init__(self, db) -> None:
        self._db = db

    def record_outcome(
        self,
        query: str,
        fact_ids: list[str],
        outcome: str,
        profile_id: str,
        context: dict | None = None,
    ) -> ActionOutcome:
        """Record the outcome of a retrieval.

        outcome: "success" (user used the facts),
                 "partial" (some facts useful),
                 "failure" (facts not helpful)
        """
        record = ActionOutcome(
            profile_id=profile_id,
            query=query,
            fact_ids=fact_ids,
            outcome=outcome,
            context=context or {},
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._db.execute(
            "INSERT INTO action_outcomes "
            "(outcome_id, profile_id, query, fact_ids_json, outcome, "
            "context_json, timestamp) VALUES (?,?,?,?,?,?,?)",
            (record.outcome_id, record.profile_id, record.query,
             json.dumps(record.fact_ids), record.outcome,
             json.dumps(record.context), record.timestamp),
        )
        return record

    def get_outcomes(
        self, profile_id: str, limit: int = 100
    ) -> list[ActionOutcome]:
        """Get recent outcomes for a profile."""
        rows = self._db.execute(
            "SELECT * FROM action_outcomes WHERE profile_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (profile_id, limit),
        )
        return [self._row_to_outcome(r) for r in rows]

    def get_success_rate(self, profile_id: str) -> float:
        """Compute overall success rate for a profile."""
        rows = self._db.execute(
            "SELECT outcome, COUNT(*) AS c FROM action_outcomes "
            "WHERE profile_id = ? GROUP BY outcome",
            (profile_id,),
        )
        counts = {dict(r)["outcome"]: dict(r)["c"] for r in rows}
        total = sum(counts.values())
        if total == 0:
            return 0.0
        success = counts.get("success", 0) + counts.get("partial", 0) * 0.5
        return success / total

    def get_fact_success_rate(self, fact_id: str, profile_id: str) -> float:
        """How often a specific fact led to successful outcomes."""
        rows = self._db.execute(
            "SELECT outcome FROM action_outcomes "
            "WHERE profile_id = ? AND fact_ids_json LIKE ?",
            (profile_id, f'%"{fact_id}"%'),
        )
        if not rows:
            return 0.5  # No data = neutral
        successes = sum(1 for r in rows if dict(r)["outcome"] == "success")
        return successes / len(rows)

    @staticmethod
    def _row_to_outcome(row) -> ActionOutcome:
        d = dict(row)
        return ActionOutcome(
            outcome_id=d["outcome_id"],
            profile_id=d["profile_id"],
            query=d.get("query", ""),
            fact_ids=json.loads(d.get("fact_ids_json", "[]")),
            outcome=d["outcome"],
            context=json.loads(d.get("context_json", "{}")),
            timestamp=d["timestamp"],
        )
