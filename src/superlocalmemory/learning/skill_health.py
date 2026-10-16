# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Read-only execution health; confidence and effectiveness stay separate."""

from contextlib import closing

from superlocalmemory.learning.skill_metrics import health_entry
from superlocalmemory.learning.skill_performance_miner import SkillPerformanceMiner
from superlocalmemory.storage.read_connection import ReadConnectionFactory


def _insights(conn, profile_id, skill_name):
    rows = conn.execute(
        "SELECT trigger_condition,action,confidence FROM behavioral_assertions "
        "WHERE profile_id=? AND category='skill_performance'",
        (profile_id,),
    ).fetchall()
    return [
        {"trigger": r["trigger_condition"], "action": r["action"], "confidence": r["confidence"]}
        for r in rows
        if not skill_name or skill_name in r["trigger_condition"]
    ]


def skill_health(db_path, profile_id, skill_name, include_history):
    with closing(ReadConnectionFactory(db_path).open()) as conn:
        miner = SkillPerformanceMiner(db_path)
        events = miner._get_skill_events(conn, profile_id)
        invocations = miner._parse_skill_invocations(events)
        metrics = miner._compute_skill_metrics(conn, profile_id, invocations)
        entries = [
            health_entry(name, values)
            for name, values in metrics.items()
            if not skill_name or name == skill_name
        ]
        for entry in entries:
            uses = [inv for inv in invocations if inv["skill_name"] == entry["name"]]
            entry["last_invoked"] = max((inv["created_at"] for inv in uses), default="")
            if include_history:
                ids = {inv["event_id"] for inv in uses}
                entry["recent_events"] = [event for event in events if event["id"] in ids][-10:]
        return {
            "skills": entries,
            "skill_count": len(entries),
            "assertion_insights": _insights(conn, profile_id, skill_name),
            "profile_id": profile_id,
        }
