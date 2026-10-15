# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""When the online ranker retrain should run.

Three defects kept the online retrain from ever firing on a real store:

* It read ``learning_model_state.metadata_json``, a column no migration
  created (M055 adds it), so the SELECT raised and the answer was "no".
* Its outcome trigger read ``new_outcomes_since_last_retrain`` from that
  column, and nothing anywhere increments it. The count is now taken from
  the data itself: labelled outcomes (``action_outcomes`` rows with a numeric
  reward that name a recall) newer than the last training.
* With no active model it deferred to the legacy cold-start path, which
  installs a model as ACTIVE with no live test. Cold start now goes through
  the same shadow cycle: the candidate is tested against the heuristic.

Two guards: never retrain while a candidate is under test (persisting a new
candidate would delete it mid-experiment), and honour the post-rollback
cooldown.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["labelled_outcomes_since", "should_retrain"]


def _parse(ts: object) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def labelled_outcomes_since(memory_db: str | Path, profile_id: str,
                            since: datetime | None) -> int:
    """Outcomes that can label a training row, newer than ``since``."""
    path = Path(str(memory_db))
    if str(memory_db) == ":memory:" or not path.exists():
        return 0
    stamp = (since or datetime(1970, 1, 1, tzinfo=timezone.utc)).isoformat()
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                               timeout=2.0)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM action_outcomes WHERE profile_id = ? "
                "AND reward IS NOT NULL AND COALESCE(recall_query_id, '') != '' "
                "AND timestamp > ?", (profile_id, stamp)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def _model_rows(learning_db: str, profile_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(learning_db, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT is_active, is_candidate, trained_at, metadata_json "
            "FROM learning_model_state WHERE profile_id = ?",
            (profile_id,)).fetchall()
    finally:
        conn.close()


def _meta(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {}
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def should_retrain(learning_db: str, memory_db: str, profile_id: str, *,
                   new_outcomes_threshold: int, hours_threshold: float) -> bool:
    """True when a retrain is due. Pure reads; never raises."""
    try:
        rows = _model_rows(learning_db, profile_id)
    except sqlite3.Error as exc:
        logger.debug("should_retrain: %s", exc)
        return False
    if any(int(r["is_candidate"] or 0) for r in rows):
        return False  # an experiment is running; let it finish
    active = next((r for r in rows if int(r["is_active"] or 0)), None)
    meta = _meta(active)
    now = datetime.now(timezone.utc)
    cooldown = _parse(meta.get("retrain_disabled_until"))
    if cooldown is not None and cooldown > now:
        return False
    if not rows:
        return True  # cold start: the shadow cycle checks data sufficiency
    trained = [t for t in (_parse(r["trained_at"]) for r in rows) if t]
    since = max([*trained, *filter(None, [_parse(meta.get("last_retrain_at"))])],
                default=None)
    counted = labelled_outcomes_since(memory_db, profile_id, since)
    try:
        stamped = int(meta.get("new_outcomes_since_last_retrain", 0) or 0)
    except (TypeError, ValueError):
        stamped = 0
    if max(counted, stamped) >= new_outcomes_threshold:
        return True
    last = _parse(meta.get("last_retrain_at"))
    if active is None or last is None:
        return False
    return (now - last).total_seconds() / 3600.0 >= hours_threshold
