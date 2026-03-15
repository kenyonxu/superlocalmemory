# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under the MIT License - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — GDPR Compliance.

Implements GDPR rights: right to access, right to erasure (forget),
right to data portability (export), and audit trail.
Profile-scoped. All operations logged to compliance_audit.

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class GDPRCompliance:
    """GDPR compliance operations for memory data.

    Supports:
    - Right to Access (Art. 15): Export all data for a profile
    - Right to Erasure (Art. 17): Delete all data for a profile/entity
    - Right to Portability (Art. 20): Export in machine-readable format
    - Audit Trail: Log all data operations
    """

    def __init__(self, db) -> None:
        self._db = db

    # -- Right to Access (Art. 15) -----------------------------------------

    def export_profile_data(self, profile_id: str) -> dict:
        """Export ALL data for a profile in machine-readable format.

        Returns a dict containing all memories, facts, entities,
        edges, trust scores, feedback, and behavioral patterns.
        """
        self._audit("export", "profile", profile_id, "Full data export")

        data: dict = {"profile_id": profile_id, "exported_at": _now()}

        # Memories
        rows = self._db.execute(
            "SELECT * FROM memories WHERE profile_id = ?", (profile_id,)
        )
        data["memories"] = [dict(r) for r in rows]

        # Facts
        rows = self._db.execute(
            "SELECT * FROM atomic_facts WHERE profile_id = ?", (profile_id,)
        )
        data["facts"] = [dict(r) for r in rows]

        # Entities
        rows = self._db.execute(
            "SELECT * FROM canonical_entities WHERE profile_id = ?", (profile_id,)
        )
        data["entities"] = [dict(r) for r in rows]

        # Graph edges
        rows = self._db.execute(
            "SELECT * FROM graph_edges WHERE profile_id = ?", (profile_id,)
        )
        data["edges"] = [dict(r) for r in rows]

        # Trust scores
        rows = self._db.execute(
            "SELECT * FROM trust_scores WHERE profile_id = ?", (profile_id,)
        )
        data["trust_scores"] = [dict(r) for r in rows]

        # Feedback
        rows = self._db.execute(
            "SELECT * FROM feedback_records WHERE profile_id = ?", (profile_id,)
        )
        data["feedback"] = [dict(r) for r in rows]

        data["total_items"] = sum(
            len(v) for v in data.values() if isinstance(v, list)
        )

        logger.info("Exported %d items for profile '%s'", data["total_items"], profile_id)
        return data

    # -- Right to Erasure (Art. 17) ----------------------------------------

    def forget_profile(self, profile_id: str) -> dict:
        """Delete ALL data for a profile (right to be forgotten).

        CASCADE deletes handle most cleanup via foreign keys.
        Returns counts of deleted items.
        """
        if profile_id == "default":
            raise ValueError("Cannot delete the default profile via GDPR erasure. "
                             "Use profile deletion instead.")

        self._audit("delete", "profile", profile_id, "GDPR erasure request")

        counts: dict[str, int] = {}
        tables = [
            "compliance_audit", "action_outcomes", "behavioral_patterns",
            "feedback_records", "trust_scores", "provenance",
            "consolidation_log", "graph_edges", "temporal_events",
            "memory_scenes", "entity_profiles",
            "atomic_facts", "memories", "canonical_entities",
        ]
        for table in tables:
            rows = self._db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE profile_id = ?",
                (profile_id,),
            )
            counts[table] = int(dict(rows[0])["c"]) if rows else 0
            self._db.execute(
                f"DELETE FROM {table} WHERE profile_id = ?", (profile_id,)
            )

        # Delete profile itself
        self._db.execute(
            "DELETE FROM profiles WHERE profile_id = ?", (profile_id,)
        )
        counts["profiles"] = 1

        logger.info("GDPR erasure for '%s': %s", profile_id, counts)
        return counts

    def forget_entity(self, entity_name: str, profile_id: str) -> dict:
        """Delete all data related to a specific entity.

        Removes facts mentioning the entity, edges, temporal events,
        and the entity itself. For targeted erasure requests.
        """
        self._audit("delete", "entity", entity_name,
                     f"GDPR entity erasure in profile {profile_id}",
                     profile_id=profile_id)

        entity = self._db.get_entity_by_name(entity_name, profile_id)
        if entity is None:
            return {"deleted": 0, "entity": entity_name, "found": False}

        eid = entity.entity_id
        counts: dict[str, int] = {}

        # Delete facts mentioning this entity
        rows = self._db.execute(
            "SELECT fact_id FROM atomic_facts WHERE profile_id = ? "
            "AND canonical_entities_json LIKE ?",
            (profile_id, f'%"{eid}"%'),
        )
        fact_ids = [dict(r)["fact_id"] for r in rows]
        for fid in fact_ids:
            self._db.delete_fact(fid)
        counts["facts"] = len(fact_ids)

        # Delete temporal events
        self._db.execute(
            "DELETE FROM temporal_events WHERE entity_id = ? AND profile_id = ?",
            (eid, profile_id),
        )

        # Delete entity profile
        self._db.execute(
            "DELETE FROM entity_profiles WHERE entity_id = ? AND profile_id = ?",
            (eid, profile_id),
        )

        # Delete aliases + entity
        self._db.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (eid,))
        self._db.execute("DELETE FROM canonical_entities WHERE entity_id = ?", (eid,))
        counts["entity"] = 1

        logger.info("Entity erasure '%s' in '%s': %s", entity_name, profile_id, counts)
        return counts

    # -- Audit Trail -------------------------------------------------------

    def get_audit_trail(
        self, profile_id: str, limit: int = 100
    ) -> list[dict]:
        """Get compliance audit trail for a profile."""
        rows = self._db.execute(
            "SELECT * FROM compliance_audit WHERE profile_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (profile_id, limit),
        )
        return [dict(r) for r in rows]

    def _audit(
        self, action: str, target_type: str, target_id: str, details: str,
        profile_id: str | None = None,
    ) -> None:
        """Log a compliance action."""
        from superlocalmemory.storage.models import _new_id
        pid = profile_id if profile_id is not None else target_id
        self._db.execute(
            "INSERT INTO compliance_audit "
            "(audit_id, profile_id, action, target_type, target_id, details, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            (_new_id(), pid, action, target_type, target_id, details, _now()),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()
