# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""What the learning loop has actually done — measured, never inferred.

Three surfaces used to answer "is it learning?" from proxies: ``session_init``
said ``status: trained`` once 200 feedback signals existed (a count of
reports, with zero models trained), and the dashboard's health
page said "All green means your memory is learning correctly" whenever the
daemon answered. This module reads the evidence each claim needs:

* ``labelled_outcomes`` — outcome reports that name a recall and carry a
  numeric reward (the only rows that can settle a play or label training);
* ``settled_from_outcomes`` — bandit plays settled by such a report (each
  one moved one arm's posterior);
* ``active_model`` / ``candidate`` — the model registry, not a counter;
* ``last_shadow_verdict`` — the latest promote/reject the live test reached.

``status`` is derived from those, in order: ``testing`` (a candidate is under
live test), ``trained`` (an active model exists), ``eligible`` (enough labelled
outcomes to train, no model yet), ``collecting`` otherwise.
``learning_demonstrated`` is True only when at least one report has settled a
play — the minimum evidence that a reported outcome changed what is learned.
Every read is bounded and read-only; a missing table reads as zero.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

__all__ = ["learning_readiness"]

#: Labelled outcomes before a first training is worth attempting; mirrors
#: ``consolidation_worker.MIN_INFORMATIVE_LABELS``.
_ELIGIBLE_AT = 50


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                               timeout=0.5)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(conn: sqlite3.Connection | None, sql: str, args: tuple) -> int:
    if conn is None:
        return 0
    try:
        row = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def _models(conn: sqlite3.Connection | None, profile_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"active_model": None, "candidate": None,
                           "last_shadow_verdict": None}
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT id, is_active, is_candidate, trained_at, promoted_at, "
            "shadow_results_json FROM learning_model_state "
            "WHERE profile_id = ? ORDER BY id DESC", (profile_id,)).fetchall()
    except sqlite3.Error:
        return out
    for row in rows:
        if row["is_active"] and out["active_model"] is None:
            out["active_model"] = {"id": row["id"], "trained_at": row["trained_at"],
                                   "promoted_at": row["promoted_at"]}
        if row["is_candidate"] and out["candidate"] is None:
            out["candidate"] = {"id": row["id"], "trained_at": row["trained_at"]}
        verdict = _verdict(row["shadow_results_json"])
        if verdict and out["last_shadow_verdict"] is None:
            out["last_shadow_verdict"] = verdict
    return out


def _verdict(raw: object) -> dict | None:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "verdict" not in data:
        return None
    keep = ("verdict", "criterion", "decided_at", "n_active", "n_candidate",
            "mean_active", "mean_candidate")
    return {k: data.get(k) for k in keep}


def _status(labelled: int, models: dict[str, Any]) -> str:
    if models["candidate"] is not None:
        return "testing"
    if models["active_model"] is not None:
        return "trained"
    return "eligible" if labelled >= _ELIGIBLE_AT else "collecting"


def learning_readiness(learning_db: Path | str, memory_db: Path | str,
                       profile_id: str) -> dict[str, Any]:
    """Evidence-backed learning state for ``profile_id``. Never raises."""
    lconn = _ro(Path(learning_db))
    mconn = _ro(Path(memory_db))
    try:
        labelled = _scalar(
            mconn, "SELECT COUNT(*) FROM action_outcomes WHERE profile_id = ? "
            "AND reward IS NOT NULL AND COALESCE(recall_query_id, '') != ''",
            (profile_id,))
        settled = _scalar(
            lconn, "SELECT COUNT(*) FROM bandit_plays WHERE profile_id = ? "
            "AND outcome_id IS NOT NULL", (profile_id,))
        exposures = _scalar(
            lconn, "SELECT COUNT(DISTINCT query_id) FROM learning_signals "
            "WHERE profile_id = ? AND signal_type IN ('candidate', 'shown')",
            (profile_id,))
        models = _models(lconn, profile_id)
    finally:
        for conn in (lconn, mconn):
            if conn is not None:
                conn.close()
    return {
        "status": _status(labelled, models),
        "learning_demonstrated": settled > 0,
        "labelled_outcomes": labelled,
        "settled_from_outcomes": settled,
        "recalls_with_exposure_features": exposures,
        **models,
    }
