# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""One inactivity policy for configured compaction and periodic decay."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from superlocalmemory.compliance.lifecycle import LifecycleManager as BaseLifecycleManager
from superlocalmemory.storage.models import MemoryLifecycle


@dataclass(frozen=True)
class InactivityPolicy:
    cold_days: int = 30
    archive_days: int = 90

    def __post_init__(self):
        if type(self.cold_days) is not int or type(self.archive_days) is not int:
            raise ValueError("Retention thresholds must be integers")
        if not 0 <= self.cold_days <= self.archive_days:
            raise ValueError("Require 0 <= cold_after_days <= archive_after_days")

    def state(self, last_accessed_at):
        try:
            dt = datetime.fromisoformat(last_accessed_at)
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
            days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            days = 0.0
        if days < min(7, self.cold_days):
            return MemoryLifecycle.ACTIVE
        if days < self.cold_days:
            return MemoryLifecycle.WARM
        if days < self.archive_days:
            return MemoryLifecycle.COLD
        return MemoryLifecycle.ARCHIVED


def configured_policy(db, profile_id):
    raw = db.get_config(f"retention_policy:{profile_id}")
    if not isinstance(raw, str):
        return None
    thresholds = json.loads(raw)
    return InactivityPolicy(thresholds["cold_days"], thresholds["archive_days"])


def set_configured_policy(db, profile_id, cold_days, archive_days):
    policy = InactivityPolicy(cold_days, archive_days)
    db.set_config(
        f"retention_policy:{profile_id}",
        json.dumps({"cold_days": policy.cold_days, "archive_days": policy.archive_days}),
    )


class LifecycleManager(BaseLifecycleManager):
    """Use genuine last access; the base class retains Langevin handling."""

    def _time_based_state(self, fact):
        from superlocalmemory.learning.forgetting_metadata import protected_fact

        if protected_fact(self._db, fact.fact_id, fact.profile_id):
            return fact.lifecycle
        rows = self._db.execute(
            "SELECT MAX(julianday(accessed_at)) AS latest FROM fact_access_log "
            "WHERE fact_id=? AND profile_id=? AND access_type!='consolidation'",
            (fact.fact_id, fact.profile_id),
        )
        latest = rows[0]["latest"] if rows else None
        timestamp = fact.created_at
        if latest is not None:
            # SQLite's Julian clock and Python's UTC clock have the same epoch.
            timestamp = datetime.fromtimestamp((latest - 2440587.5) * 86400.0, UTC).isoformat()
        return (configured_policy(self._db, fact.profile_id) or InactivityPolicy()).state(timestamp)


def apply_configured_policy(db, results, profile_id):
    policy = configured_policy(db, profile_id)
    if policy is None:
        return results
    return [
        dict(
            result,
            zone=(
                "archive"
                if policy.state(result["last_accessed_at"]) is MemoryLifecycle.ARCHIVED
                else policy.state(result["last_accessed_at"]).value
            ),
        )
        for result in results
    ]
