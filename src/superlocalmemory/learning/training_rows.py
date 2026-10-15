# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Labelled ranker-training rows: exposures joined to authentic outcomes.

WHY THIS EXISTS
------------------------------
The trainer's join read ``action_outcomes`` from learning.db. That table lives
in memory.db; learning.db has none, so the query raised, the fetch returned
zero rows, and the ranker never trained however much feedback arrived. The
integration test for that join only passed because it copied the outcome rows
into learning.db first.

THE CONTRACT
------------
* **Features** come only from exposures — ``learning_signals`` rows of an
  exposure kind with a non-synthetic ``learning_features`` row — i.e. what the
  recall path recorded (asynchronously, via the signal worker) about each
  shown candidate, keyed by the recall's ``query_id``. An exposure is never
  counted as feedback (``signal_kinds``).
* **Labels** come only from outcomes that name the recall
  (``action_outcomes.recall_query_id``) and the memory (``fact_ids_json``).
  The earliest outcome wins, matching settlement. Displayed position is never
  used as a label.
* **Groups** are queries with at least one labelled memory. Other memories
  exposed in a judged query get the neutral reward 0.5 — they were shown and
  not singled out — and are marked ``label_source = 'unjudged_exposure'``.
  Queries nobody judged are left out entirely.

memory.db is ATTACHed to the learning connection for this read only (SELECTs
only), so the join runs in SQLite and nothing is copied.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["NEUTRAL_REWARD", "fetch_labelled_rows", "outcome_source"]

#: Reward given to an exposed but unjudged memory inside a judged query.
NEUTRAL_REWARD = 0.5

_EXPOSURE_TYPES = "('candidate', 'shown', 'legacy_feedback')"


def outcome_source(conn: sqlite3.Connection, memory_db: Path | None) -> str | None:
    """Qualified name of the outcome table visible on ``conn``, or None.

    A learning.db that carries its own ``action_outcomes`` (older test
    fixtures) is used as is. Otherwise ``memory_db`` is attached as ``mem``.
    """
    local = {r[1] for r in conn.execute("PRAGMA table_info(action_outcomes)")}
    if {"reward", "recall_query_id"} <= local:
        return "action_outcomes"
    if memory_db is None or not Path(memory_db).exists():
        return None
    try:
        # A plain path, not a file: URI — a connection opened without
        # uri=True would create a file literally named "file:...".
        conn.execute("ATTACH DATABASE ? AS mem", (str(Path(memory_db)),))
    except sqlite3.Error as exc:
        logger.warning("training rows: cannot attach memory.db: %s", exc)
        return None
    cols = {r[1] for r in conn.execute("PRAGMA mem.table_info(action_outcomes)")}
    if not {"reward", "recall_query_id"} <= cols:
        return None  # no outcome table, or M006 has not run on it
    return "mem.action_outcomes"


def _judged_sql(table: str, synthetic_filter: str) -> str:
    return (
        "SELECT s.id AS signal_id, s.query_id, s.fact_id, s.position, "
        "       s.created_at, f.features_json, o.reward AS outcome_reward, "
        "       o.outcome_id, o.timestamp AS outcome_at "
        "FROM learning_signals s "
        "JOIN learning_features f "
        "  ON f.signal_id = s.id AND f.profile_id = s.profile_id "
        f"JOIN {table} o "
        "  ON o.recall_query_id = s.query_id AND o.profile_id = s.profile_id "
        " AND o.reward IS NOT NULL "
        " AND EXISTS (SELECT 1 FROM json_each(o.fact_ids_json) j "
        "             WHERE j.value = s.fact_id) "
        "WHERE s.profile_id = ? AND s.query_id != '' "
        f"  AND s.signal_type IN {_EXPOSURE_TYPES}{synthetic_filter} "
        "  AND (julianday('now') - julianday(o.timestamp)) * 86400.0 >= ? "
        "ORDER BY s.created_at DESC LIMIT ?"
    )


def _exposures_sql(n_queries: int, synthetic_filter: str) -> str:
    marks = ",".join("?" for _ in range(n_queries))
    return (
        "SELECT s.id AS signal_id, s.query_id, s.fact_id, s.position, "
        "       s.created_at, f.features_json "
        "FROM learning_signals s "
        "JOIN learning_features f "
        "  ON f.signal_id = s.id AND f.profile_id = s.profile_id "
        f"WHERE s.profile_id = ? AND s.query_id IN ({marks}) "
        f"  AND s.signal_type IN {_EXPOSURE_TYPES}{synthetic_filter}"
    )


def _features(raw: object) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _earliest_per_signal(rows: list[sqlite3.Row]) -> dict[int, dict]:
    best: dict[int, dict] = {}
    for row in rows:
        d = dict(row)
        sid = int(d["signal_id"])
        if sid not in best or str(d["outcome_at"]) < str(best[sid]["outcome_at"]):
            best[sid] = d
    return best


def fetch_labelled_rows(
    conn: sqlite3.Connection, table: str, *, profile_id: str, limit: int,
    min_outcome_age_sec: int, include_synthetic: bool = False,
) -> list[dict]:
    """Rows for judged queries only, each with a real or neutral label."""
    synthetic = "" if include_synthetic else " AND f.is_synthetic = 0"
    judged = _earliest_per_signal(conn.execute(
        _judged_sql(table, synthetic),
        (profile_id, int(min_outcome_age_sec), int(limit)),
    ).fetchall())
    qids = sorted({str(d["query_id"]) for d in judged.values()})
    out: list[dict] = []
    for d in judged.values():
        d["features"] = _features(d.pop("features_json"))
        d["label_source"] = "outcome"
        d.pop("outcome_at", None)
        out.append(d)
    for start in range(0, len(qids), 500):
        chunk = qids[start:start + 500]
        for row in conn.execute(_exposures_sql(len(chunk), synthetic),
                                (profile_id, *chunk)).fetchall():
            if int(row["signal_id"]) in judged:
                continue
            d = dict(row)
            d["features"] = _features(d.pop("features_json"))
            d.update(outcome_reward=NEUTRAL_REWARD, outcome_id=None,
                     label_source="unjudged_exposure")
            out.append(d)
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out[: max(int(limit), 0)]
