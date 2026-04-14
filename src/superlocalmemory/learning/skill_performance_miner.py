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
MIN_INVOCATIONS = 5        # Don't create assertions for skills with fewer uses
MIN_CONFIDENCE = 0.5       # Don't inject into soft prompts below this
TRACE_WINDOW = 10          # Number of tool events to look at after a Skill call
RETRY_WINDOW_SECONDS = 300 # 5 minutes — same Skill re-invoked = potential retry
REINFORCEMENT_NUDGE = 0.10 # Bayesian confidence increase per consolidation cycle


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
                conn, profile_id, skill_invocations,
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
                        conn, profile_id, pair, corr_data,
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
        self, conn: sqlite3.Connection, profile_id: str,
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

            invocations.append({
                "skill_name": skill_name,
                "session_id": event.get("session_id", ""),
                "event_id": event.get("id", 0),
                "created_at": event.get("created_at", ""),
                "project_path": event.get("project_path", ""),
            })

        return invocations

    def _compute_skill_metrics(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        invocations: list[dict],
    ) -> dict[str, dict]:
        """Compute per-skill metrics using execution trace heuristic.

        Outcome heuristic (conservative, labeled as APPROXIMATE):
        - Signal 1 (POSITIVE): Productive tools follow (Edit, Write, Bash success)
        - Signal 2 (NEGATIVE): Same Skill re-invoked within 5 min
        - Signal 3 (NEGATIVE): Bash errors in next 3 events
        - Signal 4 (WEAK POSITIVE): Session continues 10+ events

        H-N1QUERY: Batch-loads all trace events in one query instead of N+1.
        """
        metrics: dict[str, dict] = defaultdict(lambda: {
            "total_invocations": 0,
            "positive_signals": 0,
            "negative_signals": 0,
            "sessions": set(),
            "projects": set(),
        })

        if not invocations:
            return {}

        # H-N1QUERY: Batch-load all potential trace events in one query.
        # Find the min event_id across all invocations so we can fetch
        # all subsequent events in a single SELECT.
        min_event_id = min(inv["event_id"] for inv in invocations)
        all_trace_rows = conn.execute(
            "SELECT id, tool_name, event_type, output_summary, created_at "
            "FROM tool_events "
            "WHERE profile_id = ? AND id > ? "
            "ORDER BY id ASC",
            (profile_id, min_event_id),
        ).fetchall()
        all_trace = [dict(r) for r in all_trace_rows]

        # Build an index: for each event_id, find its position in all_trace
        # so we can slice TRACE_WINDOW events after it in O(1).
        trace_id_to_idx: dict[int, int] = {}
        for idx, t in enumerate(all_trace):
            if t["id"] not in trace_id_to_idx:
                trace_id_to_idx[t["id"]] = idx

        for inv in invocations:
            skill = inv["skill_name"]
            m = metrics[skill]
            m["total_invocations"] += 1
            m["sessions"].add(inv["session_id"])
            if inv["project_path"]:
                m["projects"].add(inv["project_path"])

            # Find trace window for this invocation from pre-loaded data
            # Events with id > inv["event_id"], take first TRACE_WINDOW
            start_idx = 0
            eid = inv["event_id"]
            # The first entry in all_trace with id > eid
            # Since all_trace starts at min_event_id+1 and is sorted, we
            # can bisect or scan. Use the index if the next id is present.
            # Simple approach: events after eid start at the position of eid+1
            # or the first id > eid in the sorted list.
            for candidate_id in range(eid + 1, eid + TRACE_WINDOW + 2):
                if candidate_id in trace_id_to_idx:
                    start_idx = trace_id_to_idx[candidate_id]
                    break
            else:
                # No trace events found after this invocation
                start_idx = len(all_trace)

            trace_list = all_trace[start_idx:start_idx + TRACE_WINDOW]
            outcome = self._evaluate_trace(skill, inv, trace_list, invocations)

            if outcome > 0:
                m["positive_signals"] += 1
            elif outcome < 0:
                m["negative_signals"] += 1

        # Compute final metrics per skill
        result = {}
        for skill, m in metrics.items():
            total = m["total_invocations"]
            positive = m["positive_signals"]
            negative = m["negative_signals"]

            effective_score = (positive - negative) / total if total > 0 else 0.0
            result[skill] = {
                "total_invocations": total,
                "positive_signals": positive,
                "negative_signals": negative,
                "effective_score": round(max(-1.0, min(1.0, effective_score)), 3),
                "session_count": len(m["sessions"]),
                "project_count": len(m["projects"]),
            }

        return result

    def _evaluate_trace(
        self,
        skill_name: str,
        invocation: dict,
        trace: list[dict],
        all_invocations: list[dict],
    ) -> int:
        """Evaluate execution trace after a Skill call. Returns +1, 0, or -1."""
        if not trace:
            return 0

        score = 0

        # Signal 1: Productive tools in trace → +1
        productive_tools = {"Edit", "Write"}
        if any(t["tool_name"] in productive_tools for t in trace[:TRACE_WINDOW]):
            score += 1

        # Signal 2: Same Skill re-invoked within RETRY_WINDOW → -1
        inv_time = invocation.get("created_at", "")
        for other in all_invocations:
            if other["event_id"] == invocation["event_id"]:
                continue
            if other["skill_name"] != skill_name:
                continue
            if other["session_id"] != invocation["session_id"]:
                continue
            try:
                t1 = datetime.fromisoformat(inv_time.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(
                    other["created_at"].replace("Z", "+00:00"),
                )
                delta = abs((t2 - t1).total_seconds())
                if 0 < delta <= RETRY_WINDOW_SECONDS:
                    score -= 1
                    break
            except (ValueError, TypeError):
                pass

        # Signal 3: Bash errors in first 3 events → -1
        for t in trace[:3]:
            if t["tool_name"] == "Bash":
                output = t.get("output_summary", "")
                if output and any(
                    kw in output.lower()
                    for kw in ("error", "failed", "command not found", "permission denied")
                ):
                    score -= 1
                    break

        # Clamp to [-1, +1]
        return max(-1, min(1, score))

    def _detect_skill_correlations(
        self, invocations: list[dict],
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
            "SELECT id, confidence FROM behavioral_assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()

        confidence = min(0.85, max(0.3, abs(eff) * 0.8 + total / 100))

        if existing:
            old_conf = dict(existing)["confidence"]
            new_conf = old_conf + (1.0 - old_conf) * REINFORCEMENT_NUDGE
            conn.execute(
                "UPDATE behavioral_assertions SET "
                "action = ?, confidence = ?, evidence_count = ?, "
                "reinforcement_count = reinforcement_count + 1, "
                "last_reinforced_at = ?, updated_at = ? WHERE id = ?",
                (action, round(min(0.95, new_conf), 4), total, now, now, assertion_id),
            )
            return "reinforced"
        else:
            conn.execute(
                "INSERT INTO behavioral_assertions "
                "(id, profile_id, project_path, trigger_condition, action, "
                " category, confidence, evidence_count, source, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, 'skill_performance', ?, ?, 'skill_miner', ?, ?)",
                (assertion_id, profile_id, trigger, action,
                 round(confidence, 4), total, now, now),
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
            "SELECT id FROM behavioral_assertions WHERE id = ?",
            (assertion_id,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE behavioral_assertions SET "
                "action = ?, reinforcement_count = reinforcement_count + 1, "
                "last_reinforced_at = ?, updated_at = ? WHERE id = ?",
                (action, now, now, assertion_id),
            )
        else:
            conn.execute(
                "INSERT INTO behavioral_assertions "
                "(id, profile_id, project_path, trigger_condition, action, "
                " category, confidence, evidence_count, source, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, 'skill_correlation', ?, ?, 'skill_miner', ?, ?)",
                (assertion_id, profile_id, trigger, action,
                 round(min(0.7, corr_data["count"] / 10), 4),
                 corr_data["count"], now, now),
            )
