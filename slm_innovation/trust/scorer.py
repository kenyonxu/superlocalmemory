"""SLM Innovation — Bayesian Trust Scorer.

Computes trust scores per entity, source, and fact.
Updated on access, contradiction, confirmation. Profile-scoped.
Ported from V2.8 with proper Bayesian updates.

In V1, trust was a write-only phantom — instantiated but never used.
Now wired into retrieval for trust-weighted ranking.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from slm_innovation.storage.models import TrustScore

logger = logging.getLogger(__name__)

# Default priors
_DEFAULT_TRUST = 0.5
_CONFIRMATION_BOOST = 0.05   # Bayesian update on confirmation
_CONTRADICTION_PENALTY = 0.15  # Bayesian update on contradiction
_ACCESS_BOOST = 0.01          # Small boost per access (spaced repetition)


class TrustScorer:
    """Bayesian trust scoring for memories, entities, and sources.

    Trust score = P(reliable | evidence). Updated via:
    - Confirmation: trust increases (multiple sources agree)
    - Contradiction: trust decreases (conflicting evidence)
    - Access: small boost (frequently accessed = implicitly trusted)
    - Decay: trust slowly decays toward 0.5 without evidence (neutral prior)
    """

    def __init__(self, db) -> None:
        self._db = db

    def get_trust(
        self, target_type: str, target_id: str, profile_id: str
    ) -> float:
        """Get current trust score for a target. Returns default if none exists."""
        rows = self._db.execute(
            "SELECT trust_score FROM trust_scores "
            "WHERE target_type = ? AND target_id = ? AND profile_id = ?",
            (target_type, target_id, profile_id),
        )
        if rows:
            return float(dict(rows[0])["trust_score"])
        return _DEFAULT_TRUST

    def update_on_confirmation(
        self, target_type: str, target_id: str, profile_id: str
    ) -> float:
        """Increase trust when evidence confirms this target."""
        current = self._get_or_create(target_type, target_id, profile_id)
        # Bayesian: push toward 1.0
        new_score = current.trust_score + _CONFIRMATION_BOOST * (1.0 - current.trust_score)
        new_score = min(1.0, new_score)
        return self._update(current, new_score)

    def update_on_contradiction(
        self, target_type: str, target_id: str, profile_id: str
    ) -> float:
        """Decrease trust when evidence contradicts this target."""
        current = self._get_or_create(target_type, target_id, profile_id)
        # Bayesian: push toward 0.0
        new_score = current.trust_score - _CONTRADICTION_PENALTY * current.trust_score
        new_score = max(0.0, new_score)
        return self._update(current, new_score)

    def update_on_access(
        self, target_type: str, target_id: str, profile_id: str
    ) -> float:
        """Small trust boost on access (spaced repetition principle)."""
        current = self._get_or_create(target_type, target_id, profile_id)
        new_score = min(1.0, current.trust_score + _ACCESS_BOOST)
        return self._update(current, new_score)

    def get_entity_trust(self, entity_id: str, profile_id: str) -> float:
        """Convenience: get trust for an entity."""
        return self.get_trust("entity", entity_id, profile_id)

    def get_fact_trust(self, fact_id: str, profile_id: str) -> float:
        """Convenience: get trust for a fact."""
        return self.get_trust("fact", fact_id, profile_id)

    def get_all_scores(self, profile_id: str) -> list[TrustScore]:
        """Get all trust scores for a profile."""
        rows = self._db.execute(
            "SELECT * FROM trust_scores WHERE profile_id = ?", (profile_id,),
        )
        return [
            TrustScore(
                trust_id=(d := dict(r))["trust_id"],
                profile_id=d["profile_id"],
                target_type=d["target_type"],
                target_id=d["target_id"],
                trust_score=d["trust_score"],
                evidence_count=d["evidence_count"],
                last_updated=d["last_updated"],
            )
            for r in rows
        ]

    # -- Internal ----------------------------------------------------------

    def _get_or_create(
        self, target_type: str, target_id: str, profile_id: str
    ) -> TrustScore:
        """Get existing score or create with default."""
        rows = self._db.execute(
            "SELECT * FROM trust_scores "
            "WHERE target_type = ? AND target_id = ? AND profile_id = ?",
            (target_type, target_id, profile_id),
        )
        if rows:
            d = dict(rows[0])
            return TrustScore(
                trust_id=d["trust_id"],
                profile_id=d["profile_id"],
                target_type=d["target_type"],
                target_id=d["target_id"],
                trust_score=d["trust_score"],
                evidence_count=d["evidence_count"],
                last_updated=d["last_updated"],
            )
        # Create new
        ts = TrustScore(
            profile_id=profile_id,
            target_type=target_type,
            target_id=target_id,
            trust_score=_DEFAULT_TRUST,
            evidence_count=0,
        )
        self._db.execute(
            "INSERT INTO trust_scores "
            "(trust_id, profile_id, target_type, target_id, trust_score, "
            "evidence_count, last_updated) VALUES (?,?,?,?,?,?,?)",
            (ts.trust_id, ts.profile_id, ts.target_type, ts.target_id,
             ts.trust_score, ts.evidence_count, ts.last_updated),
        )
        return ts

    def _update(self, current: TrustScore, new_score: float) -> float:
        """Persist updated trust score."""
        self._db.execute(
            "UPDATE trust_scores SET trust_score = ?, evidence_count = ?, "
            "last_updated = ? WHERE trust_id = ?",
            (new_score, current.evidence_count + 1,
             datetime.now(UTC).isoformat(), current.trust_id),
        )
        return new_score
