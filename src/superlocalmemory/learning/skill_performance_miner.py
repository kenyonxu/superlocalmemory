# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Skill Performance Miner — tracks per-skill effectiveness from tool events.

Zero-LLM approach: mines tool_events table for Skill tool invocations,
builds execution traces from surrounding events, computes approximate
outcome heuristics, and creates skill-level behavioral assertions.

Runs as Step 10 in the consolidation pipeline (after Step 9: soft prompts).
Depends on enriched tool_events (v3.4.10 hook with input_summary/output_summary).

Part of Qualixar | Author: Varun Pratap Bhardwaj
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Thresholds — conservative to avoid hallucinating patterns
MIN_INVOCATIONS = 5  # Don't create assertions for skills with fewer uses
MIN_CONFIDENCE = 0.5  # Don't inject into soft prompts below this
TRACE_WINDOW = 10  # Number of tool events to look at after a Skill call
RETRY_WINDOW_SECONDS = 300  # 5 minutes — same Skill re-invoked = potential retry
REINFORCEMENT_NUDGE = 0.10  # Bayesian confidence increase per consolidation cycle


class SkillPerformanceMiner:
    """Mine tool_events for per-skill performance metrics.

    Discovers patterns like:
    - "brainstorming skill: 82% effective, 47 invocations, best for feature planning"
    - "TDD + code-review used together: +23% effective vs individually"
    - "brainstorm skill degraded: effective_rate dropped from 0.82 to 0.35"
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)

    def mine(self, profile_id: str = "default") -> dict:
        """Run skill performance mining. Returns summary."""
        result = {
            "skills_found": 0,
            "assertions_created": 0,
            "assertions_reinforced": 0,
            "entities_updated": 0,
        }

        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row

        try:
            # Step 1: Find all Skill tool invocations
            skill_events = self._get_skill_events(conn, profile_id)
            if not skill_events:
                return result

            # Step 2: Extract skill names from input_summary
            skill_invocations = self._parse_skill_invocations(skill_events)
            result["skills_found"] = len(set(s["skill_name"] for s in skill_invocations))

            if not skill_invocations:
                return result

            # Step 3: Build execution traces and compute outcomes
            skill_metrics = self._compute_skill_metrics(
                conn,
                profile_id,
                skill_invocations,
            )

            # Step 4: Create/update behavioral assertions for each skill
            for skill_name, metrics in skill_metrics.items():
                if metrics["total_invocations"] < MIN_INVOCATIONS:
                    continue

                r = self._upsert_skill_assertion(conn, profile_id, skill_name, metrics)
                result[f"assertions_{r}"] = result.get(f"assertions_{r}", 0) + 1

            # Step 5: Detect skill correlations (pairs used together)
            correlations = self._detect_skill_correlations(skill_invocations)
            for pair, corr_data in correlations.items():
                if corr_data["count"] >= 3:
                    self._upsert_correlation_assertion(
                        conn,
                        profile_id,
                        pair,
                        corr_data,
                    )

            conn.commit()
        except Exception as exc:
            logger.warning("Skill performance mining failed: %s", exc)
            result["error"] = str(exc)
        finally:
            conn.close()

        logger.info(
            "Skill performance mining: %d skills, %d assertions",
            result["skills_found"],
            result.get("assertions_created", 0) + result.get("assertions_reinforced", 0),
        )
        return result

    def _get_skill_events(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
    ) -> list[dict]:
        """Get all Skill tool events with enriched data."""
        rows = conn.execute(
            "SELECT id, session_id, tool_name, event_type, input_summary, "
            "output_summary, project_path, created_at "
            "FROM tool_events "
            "WHERE profile_id = ? AND tool_name = 'Skill' "
            "ORDER BY created_at ASC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _parse_skill_invocations(self, skill_events: list[dict]) -> list[dict]:
        """Extract skill name and args from input_summary JSON."""
        invocations = []

        for event in skill_events:
            input_raw = event.get("input_summary", "")
            output_raw = event.get("output_summary", "")
            skill_name = ""

            # Try extracting from input_summary (enriched hook format)
            if input_raw:
                try:
                    inp = json.loads(input_raw) if input_raw.startswith("{") else {}
                    skill_name = inp.get("skill", "")
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fallback: try output_summary (ECC ingestion format)
            if not skill_name and output_raw:
                try:
                    out = json.loads(output_raw) if output_raw.startswith("{") else {}
                    skill_name = out.get("commandName", "")
                except (json.JSONDecodeError, TypeError):
                    pass

            if not skill_name:
                continue

            invocations.append(
                {
                    "skill_name": skill_name,
                    "session_id": event.get("session_id", ""),
                    "event_id": event.get("id", 0),
                    "created_at": event.get("created_at", ""),
                    "project_path": event.get("project_path", ""),
                }
            )

        return invocations

    def _compute_skill_metrics(self, conn, profile_id, invocations) -> dict[str, dict]:
        """One execution window per invocation, bounded to its session/project."""
        from superlocalmemory.learning.skill_metrics import execution_windows

        traces = execution_windows(conn, profile_id, invocations, TRACE_WINDOW)
        metrics = defaultdict(
            lambda: {
                "total_invocations": 0,
                "positive_signals": 0,
                "negative_signals": 0,
                "sessions": set(),
                "projects": set(),
            }
        )
        for invocation in invocations:
            skill = invocation["skill_name"]
            current = metrics[skill]
            current["total_invocations"] += 1
            current["sessions"].add(invocation["session_id"])
            if invocation["project_path"]:
                current["projects"].add(invocation["project_path"])
            outcome = self._evaluate_trace(
                skill, invocation, traces[invocation["event_id"]], invocations
            )
            if outcome:
                current["positive_signals" if outcome > 0 else "negative_signals"] += 1
        return {
            skill: {
                "total_invocations": current["total_invocations"],
                "positive_signals": current["positive_signals"],
                "negative_signals": current["negative_signals"],
                "effective_score": round(
                    (current["positive_signals"] - current["negative_signals"])
                    / current["total_invocations"],
                    3,
                ),
                "session_count": len(current["sessions"]),
                "project_count": len(current["projects"]),
            }
            for skill, current in metrics.items()
        }

    def _evaluate_trace(self, skill_name, invocation, trace, all_invocations) -> int:
        """Failures dominate heuristic productivity; no execution is unknown."""
        if not trace:
            return 0
        if any(
            event.get("event_type") == "error"
            or (
                event["tool_name"] == "Bash"
                and any(
                    word in event.get("output_summary", "").lower()
                    for word in ("error", "failed", "command not found", "permission denied")
                )
            )
            for event in trace
        ):
            return -1
        positive = any(
            event["tool_name"] in {"Edit", "Write"}
            or (event["tool_name"] == "Bash" and event.get("event_type") == "success")
            for event in trace[:TRACE_WINDOW]
        )
        return int(positive) - int(_same_skill_retry(skill_name, invocation, all_invocations))

    def _detect_skill_correlations(
        self,
        invocations: list[dict],
    ) -> dict[tuple[str, str], dict]:
        """Find skills frequently used together in the same session."""
        session_skills: dict[str, set[str]] = defaultdict(set)
        for inv in invocations:
            session_skills[inv["session_id"]].add(inv["skill_name"])

        pair_counts: Counter = Counter()
        for skills in session_skills.values():
            skill_list = sorted(skills)
            for i in range(len(skill_list)):
                for j in range(i + 1, len(skill_list)):
                    pair_counts[(skill_list[i], skill_list[j])] += 1

        return {
            pair: {"count": count, "sessions": count}
            for pair, count in pair_counts.most_common(10)
            if count >= 2
        }

    def _upsert_skill_assertion(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        skill_name: str,
        metrics: dict,
    ) -> str:
        """Create or reinforce a skill performance assertion."""
        now = datetime.now(timezone.utc).isoformat()
        eff = metrics["effective_score"]
        total = metrics["total_invocations"]

        trigger = f"when considering skill {skill_name}"
        action = (
            f"effective score: {eff:.0%} (approximate, {total} invocations, "
            f"{metrics['session_count']} sessions)"
        )

        assertion_id = hashlib.sha256(
            f"{profile_id}:skill_perf:{skill_name}".encode(),
        ).hexdigest()[:32]

        existing = conn.execute(
            "SELECT id, confidence, evidence_count, source FROM behavioral_assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()

        confidence = min(0.85, max(0.3, abs(eff) * 0.8 + total / 100))

        if existing:
            row = dict(existing)
            # Idempotent: a rejected rule stays rejected, and a
            # re-run over the same evidence changes nothing. Confidence is a
            # function of the evidence, not of how often mining ran.
            if row.get("source") == "rejected":
                return "suppressed"
            if int(total) <= int(row.get("evidence_count") or 0):
                return "unchanged"
            conn.execute(
                "UPDATE behavioral_assertions SET "
                "action = ?, confidence = ?, evidence_count = ?, "
                "updated_at = ? WHERE id = ?",
                (action, round(confidence, 4), total, now, assertion_id),
            )
            return "reinforced"
        else:
            conn.execute(
                "INSERT INTO behavioral_assertions "
                "(id, profile_id, project_path, trigger_condition, action, "
                " category, confidence, evidence_count, source, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, 'skill_performance', ?, ?, 'skill_miner', ?, ?)",
                (assertion_id, profile_id, trigger, action, round(confidence, 4), total, now, now),
            )
            return "created"

    def _upsert_correlation_assertion(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        pair: tuple[str, str],
        corr_data: dict,
    ) -> None:
        """Create assertion for skill correlation."""
        now = datetime.now(timezone.utc).isoformat()
        trigger = f"when using {pair[0]}"
        action = f"often paired with {pair[1]} ({corr_data['count']} sessions together)"

        assertion_id = hashlib.sha256(
            f"{profile_id}:skill_corr:{pair[0]}:{pair[1]}".encode(),
        ).hexdigest()[:32]

        existing = conn.execute(
            "SELECT id, evidence_count, source FROM behavioral_assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()

        if existing:
            row = dict(existing)
            if row.get("source") != "rejected" and int(corr_data["count"]) > int(
                row.get("evidence_count") or 0
            ):
                conn.execute(
                    "UPDATE behavioral_assertions SET action = ?, "
                    "evidence_count = ?, updated_at = ? WHERE id = ?",
                    (action, int(corr_data["count"]), now, assertion_id),
                )
        else:
            conn.execute(
                "INSERT INTO behavioral_assertions "
                "(id, profile_id, project_path, trigger_condition, action, "
                " category, confidence, evidence_count, source, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, 'skill_correlation', ?, ?, 'skill_miner', ?, ?)",
                (
                    assertion_id,
                    profile_id,
                    trigger,
                    action,
                    round(min(0.7, corr_data["count"] / 10), 4),
                    corr_data["count"],
                    now,
                    now,
                ),
            )


def _same_skill_retry(skill_name, invocation, invocations):
    for other in invocations:
        if other["event_id"] == invocation["event_id"] or other["skill_name"] != skill_name:
            continue
        if other["session_id"] != invocation["session_id"]:
            continue
        try:
            first = datetime.fromisoformat(invocation.get("created_at", "").replace("Z", "+00:00"))
            second = datetime.fromisoformat(other["created_at"].replace("Z", "+00:00"))
            if 0 < abs((second - first).total_seconds()) <= RETRY_WINDOW_SECONDS:
                return True
        except (ValueError, TypeError):
            continue
    return False
