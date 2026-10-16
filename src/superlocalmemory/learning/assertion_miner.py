# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Behavioral Assertion Miner — learned patterns from tool events.

Zero-LLM: frequency, conditional frequency and temporal clustering. Runs as a
consolidation step. Two correctness invariants, both broken before 4.1.25:

IDEMPOTENT
-------------------------
Mining the same evidence twice must leave every assertion exactly as it was.
The miner used to nudge confidence toward 1 and bump ``reinforcement_count``
on EVERY run, so eight runs over 24 unchanged events took a rule from 0.52 to
0.85 and a frequency fact past the 0.5 soft-prompt threshold. Now:

* An assertion's id comes from a stable pattern key (kind, tools, project) —
  not from its display text, which embeds counts and changed with every run.
* Its confidence is a pure function of the evidence and of EXPLICIT feedback
  (``reinforce_assertion`` / ``contradict_assertion`` counts): a run with no
  new evidence writes nothing at all.
* The miner never touches ``reinforcement_count``; that counter now means
  explicit reinforcement only.

REJECTION IS DURABLE
--------------------
``contradict_assertion`` used to DELETE a rule once its confidence fell below
0.2, and the next mining pass recreated the same deterministic id from the same
events. A rejected rule is now a tombstone (``source = 'rejected'``,
confidence 0) and the miner leaves it alone; only an explicit
``reinforce_assertion`` reinstates it.

SCOPED
---------------------
Tool sequences were bigrams over every event in the profile, ordered globally,
so interleaved sessions from different agents and projects "showed" a Read ->
Edit workflow that no session contained. Transitions are now counted only
between consecutive events of ONE session in ONE project, at most
``MAX_GAP_SEC`` apart; a rule needs ``MIN_EVIDENCE`` transitions from at least
``MIN_SESSIONS`` sessions, its strength is the conditional frequency
``P(B | A)``, and it is stored under that project's ``project_path``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_EVIDENCE = 3  # Minimum events to create an assertion
MIN_SESSIONS = 2  # Distinct sessions a sequence must appear in
MAX_GAP_SEC = 1800.0  # Consecutive events further apart are not a sequence
MIN_CONDITIONAL = 0.3  # P(B|A) below this is not a workflow
MAX_ASSERTIONS_PER_RUN = 20  # Cap assertions per mining cycle
REINFORCEMENT_NUDGE = 0.15  # Explicit reinforcement step (tools_learning)
CONTRADICTION_DECAY = 0.7  # Explicit contradiction factor (tools_learning)
AUTO_CONFIDENCE_CAP = 0.7  # Evidence alone never exceeds this
PROMOTION_MIN_PROJECTS = 2  # Minimum projects for cross-project promotion
PROMOTION_MIN_CONFIDENCE = 0.8  # Minimum avg confidence for promotion
REJECTED = "rejected"


def assertion_id(profile_id: str, project_path: str, key: str) -> str:
    """Stable id for a pattern: never derived from display text."""
    return hashlib.sha256(
        f"{profile_id}\x1f{project_path}\x1f{key}".encode()).hexdigest()[:16]


def effective_confidence(base: float, reinforcements: int,
                         contradictions: int) -> float:
    """Evidence-derived confidence adjusted by explicit feedback counts."""
    capped = max(0.0, min(AUTO_CONFIDENCE_CAP, float(base)))
    lifted = 1.0 - (1.0 - capped) * (1.0 - REINFORCEMENT_NUDGE) ** max(0, reinforcements)
    return round(lifted * CONTRADICTION_DECAY ** max(0, contradictions), 4)


def _parse(ts: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AssertionMiner:
    """Mine tool_events for behavioral assertions (see module docstring)."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)

    def mine(self, profile_id: str = "default") -> dict:
        """Run all strategies. Returns created/updated/unchanged/suppressed."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        results: dict = {"created": 0, "reinforced": 0, "updated": 0,
                         "unchanged": 0, "suppressed": 0, "strategies": {}}
        strategies = (
            ("tool_sequences", self._mine_tool_sequences),
            ("corrections", self._mine_correction_patterns),
            ("tool_preferences", self._mine_tool_preferences),
            ("temporal", self._mine_temporal_patterns),
            ("cross_project", self._promote_cross_project),
        )
        try:
            for name, strategy in strategies:
                out = strategy(conn, profile_id)
                results["strategies"][name] = out
                for key in ("created", "updated", "unchanged", "suppressed"):
                    results[key] += out.get(key, 0)
                results["created"] += out.get("promoted", 0)
            conn.commit()
        except Exception as exc:
            logger.warning("Assertion mining failed: %s", exc)
            results["error"] = str(exc)
        finally:
            conn.close()
        # "reinforced" is kept for callers; it now means "new evidence".
        results["reinforced"] = results["updated"]
        logger.info("Assertion mining: %d created, %d updated, %d unchanged",
                    results["created"], results["updated"], results["unchanged"])
        return results

    # ------------------------------------------------------------------
    # Strategy 1: tool sequences, inside one session of one project
    # ------------------------------------------------------------------

    @staticmethod
    def _transitions(rows: list[sqlite3.Row]) -> dict[str, dict]:
        """Per project: A->B counts, sessions per pair, and A totals."""
        traces: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            traces[(str(row["project_path"] or ""),
                    str(row["session_id"] or ""))].append(row)
        out: dict[str, dict] = defaultdict(lambda: {
            "pairs": Counter(), "sessions": defaultdict(set), "from": Counter()})
        for (project, session), trace in traces.items():
            if not session:
                continue  # an unattributed event belongs to no workflow
            for prev, cur in zip(trace, trace[1:]):
                a, b = prev["tool_name"], cur["tool_name"]
                t0, t1 = _parse(prev["created_at"]), _parse(cur["created_at"])
                if a == b or t0 is None or t1 is None:
                    continue
                if (t1 - t0).total_seconds() > MAX_GAP_SEC:
                    continue
                bucket = out[project]
                bucket["pairs"][(a, b)] += 1
                bucket["sessions"][(a, b)].add(session)
                bucket["from"][a] += 1
        return out

    def _mine_tool_sequences(self, conn: sqlite3.Connection, profile_id: str) -> dict:
        """Repeated A->B transitions within one session/project trace."""
        result: dict = {}
        rows = conn.execute(
            "SELECT session_id, project_path, tool_name, created_at "
            "FROM tool_events WHERE profile_id = ? "
            "ORDER BY session_id, project_path, created_at, id LIMIT 20000",
            (profile_id,),
        ).fetchall()
        for project, stats in self._transitions(rows).items():
            for (tool_a, tool_b), count in stats["pairs"].most_common(10):
                n_sessions = len(stats["sessions"][(tool_a, tool_b)])
                if count < MIN_EVIDENCE or n_sessions < MIN_SESSIONS:
                    continue
                p_b_given_a = count / stats["from"][tool_a]
                if p_b_given_a < MIN_CONDITIONAL:
                    continue
                # Count-free text: the numbers live in confidence and
                # evidence_count, so the same rule reads the same in every
                # project and can be promoted across them.
                action = f"typically follow with {tool_b}"
                r = self._upsert_assertion(
                    conn, profile_id, key=f"seq:{tool_a}->{tool_b}",
                    trigger=f"when using {tool_a}", action=action,
                    category="tool_preference", evidence_count=count,
                    confidence=p_b_given_a, project_path=project,
                )
                result[r] = result.get(r, 0) + 1
        return result

    # ------------------------------------------------------------------
    # Strategy 2: correction patterns
    # ------------------------------------------------------------------

    def _mine_correction_patterns(self, conn: sqlite3.Connection, profile_id: str) -> dict:
        """Tools whose output is corrected often."""
        result: dict = {}
        rows = conn.execute(
            "SELECT tool_name, "
            "SUM(CASE WHEN event_type = 'correction' THEN 1 ELSE 0 END) AS corr, "
            "COUNT(*) AS total FROM tool_events WHERE profile_id = ? "
            "GROUP BY tool_name", (profile_id,),
        ).fetchall()
        for row in rows:
            total, corr = int(row["total"]), int(row["corr"] or 0)
            if total < MIN_EVIDENCE or corr / total <= 0.15:
                continue
            rate = corr / total
            r = self._upsert_assertion(
                conn, profile_id, key=f"corrections:{row['tool_name']}",
                trigger=f"when using {row['tool_name']}",
                action=(f"high correction rate ({corr}/{total} = {rate:.0%})"
                        " — review output before accepting"),
                category="workflow", evidence_count=corr,
                confidence=min(0.85, rate),
            )
            result[r] = result.get(r, 0) + 1
        return result

    # ------------------------------------------------------------------
    # Strategy 3: tool preference
    # ------------------------------------------------------------------

    def _mine_tool_preferences(self, conn: sqlite3.Connection, profile_id: str) -> dict:
        """Dominant tools across the profile (a count, not a workflow)."""
        result: dict = {}
        rows = conn.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM tool_events "
            "WHERE profile_id = ? GROUP BY tool_name ORDER BY cnt DESC LIMIT 10",
            (profile_id,),
        ).fetchall()
        total = sum(r["cnt"] for r in rows)
        for row in rows[:5]:
            count = int(row["cnt"])
            pct = count / total if total else 0.0
            if count < MIN_EVIDENCE or pct <= 0.1:
                continue
            r = self._upsert_assertion(
                conn, profile_id, key=f"uses:{row['tool_name']}",
                trigger="general workflow",
                action=(f"frequently uses {row['tool_name']} ({count} times, "
                        f"{pct:.0%} of all tool usage)"),
                category="tool_preference", evidence_count=count,
                confidence=min(0.8, pct),
            )
            result[r] = result.get(r, 0) + 1
        return result

    # ------------------------------------------------------------------
    # Strategy 4: temporal
    # ------------------------------------------------------------------

    def _mine_temporal_patterns(self, conn: sqlite3.Connection, profile_id: str) -> dict:
        """Peak hours of activity."""
        result: dict = {}
        rows = conn.execute(
            "SELECT created_at FROM tool_events "
            "WHERE profile_id = ? AND created_at IS NOT NULL LIMIT 5000",
            (profile_id,),
        ).fetchall()
        hours = Counter(dt.hour for dt in (_parse(r["created_at"]) for r in rows)
                        if dt is not None)
        total = sum(hours.values())
        if total < MIN_EVIDENCE:
            return result
        peaks = sorted(h for h, c in hours.items() if c / total > 0.15)
        if not peaks:
            return result
        r = self._upsert_assertion(
            conn, profile_id, key="temporal:peak_hours",
            trigger="session scheduling",
            action=(f"peak activity hours: {', '.join(f'{h}:00' for h in peaks)}"
                    f" (based on {total} events)"),
            category="workflow", evidence_count=total, confidence=0.6,
        )
        result[r] = 1
        return result

    # ------------------------------------------------------------------
    # Strategy 5: cross-project promotion
    # ------------------------------------------------------------------

    def _promote_cross_project(self, conn: sqlite3.Connection, profile_id: str) -> dict:
        """A pattern held in 2+ projects with high confidence becomes global.

        Deterministic like the rest: the global row is recomputed from its
        sources only when their evidence changed, and a rejected global rule
        is never revived by promotion.
        """
        result = {"promoted": 0, "candidates": 0}
        rows = conn.execute(
            "SELECT trigger_condition, action, category, "
            "COUNT(DISTINCT project_path) AS projects, "
            "AVG(confidence) AS avg_conf, SUM(evidence_count) AS evidence "
            "FROM behavioral_assertions "
            "WHERE profile_id = ? AND project_path != '' "
            "AND COALESCE(source, '') != ? "
            "GROUP BY trigger_condition, action HAVING projects >= ?",
            (profile_id, REJECTED, PROMOTION_MIN_PROJECTS),
        ).fetchall()
        result["candidates"] = len(rows)
        for row in rows:
            if row["avg_conf"] < PROMOTION_MIN_CONFIDENCE:
                continue
            outcome = self._upsert_assertion(
                conn, profile_id,
                key=f"global:{row['trigger_condition']}\x1f{row['action']}",
                trigger=row["trigger_condition"], action=row["action"],
                category=row["category"], evidence_count=int(row["evidence"]),
                confidence=min(0.9, float(row["avg_conf"])),
                source="cross_project", cap=0.9,
            )
            if outcome == "created":
                result["promoted"] += 1
        return result

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def _upsert_assertion(
        self, conn: sqlite3.Connection, profile_id: str, *, key: str,
        trigger: str, action: str, category: str, evidence_count: int,
        confidence: float, project_path: str = "", source: str = "auto",
        cap: float = AUTO_CONFIDENCE_CAP,
    ) -> str:
        """Create, update on NEW evidence, or leave alone. Never nudges."""
        now = datetime.now(timezone.utc).isoformat()
        aid = assertion_id(profile_id, project_path, key)
        base = min(cap, float(confidence))
        row = conn.execute(
            "SELECT evidence_count, reinforcement_count, contradiction_count, "
            "source FROM behavioral_assertions WHERE id = ? AND profile_id = ?",
            (aid, profile_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO behavioral_assertions (id, profile_id, project_path,"
                " trigger_condition, action, category, confidence, evidence_count,"
                " source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, profile_id, project_path, trigger, action, category,
                 round(base, 4), int(evidence_count), source, now, now),
            )
            return "created"
        if row["source"] == REJECTED:
            return "suppressed"
        if int(evidence_count) <= int(row["evidence_count"] or 0):
            return "unchanged"
        conf = effective_confidence(base, int(row["reinforcement_count"] or 0),
                                    int(row["contradiction_count"] or 0))
        conn.execute(
            "UPDATE behavioral_assertions SET action = ?, confidence = ?, "
            "evidence_count = ?, updated_at = ? WHERE id = ? AND profile_id = ?",
            (action, conf, int(evidence_count), now, aid, profile_id),
        )
        return "updated"
