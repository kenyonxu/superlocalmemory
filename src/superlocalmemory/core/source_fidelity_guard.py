# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Withhold unsupported derived assertions while retaining their source.

Deterministic names, attribution, numbers, dates, polarity and qualifier
checks run before a derived row can enter answers. Failed checks quarantine
that row reversibly and record the reason in derivation lineage. Exact raw
source spans and mechanically verified clamped source excerpts remain usable.
The user's explicit release decision is preserved across later checkpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from superlocalmemory.encoding.source_fidelity import check_fact_against_source

logger = logging.getLogger(__name__)

#: Prefix of ``derivation_lineage.unresolved_reason`` for a withheld fact.
REASON_PREFIX = "source_fidelity:"
#: Prefix once the user released it (``slm db fidelity --release``).
RELEASED_PREFIX = "source_fidelity_released:"
#: Prefix for a fact that stays in answers, marked unverified.
FLAGGED_PREFIX = "source_fidelity_unverified:"
#: Deterministic failures withheld from answers; originals remain retrievable.
WITHHOLD_REASONS = frozenset({
    "number_became_date", "unsupported_date", "unsupported_number",
    "unsupported_entity", "unsupported_assertion", "attribution_changed",
    "negation_lost", "negation_added", "order_reversed", "status_lost",
})


def _released_reason(db: Any, profile_id: str, fact_id: str) -> str | None:
    try:
        rows = db.execute(
            "SELECT unresolved_reason FROM derivation_lineage "
            "WHERE profile_id = ? AND object_type = 'fact' AND object_id = ? "
            "AND unresolved_reason LIKE ? LIMIT 1",
            (profile_id, fact_id, RELEASED_PREFIX + "%"),
        )
    except Exception:
        return None
    return str(dict(rows[0])["unresolved_reason"]) if rows else None


def _has_quarantine_column(db: Any) -> bool:
    try:
        rows = db.execute("PRAGMA table_info(atomic_facts)")
    except Exception:
        return False
    return any(dict(row).get("name") == "quarantined" for row in rows)


def judge_fidelity(
    db: Any,
    *,
    profile_id: str,
    fact_id: str,
    content: str,
    raw_content: str,
) -> tuple[str | None, bool]:
    """``(lineage reason or None, withhold?)``, with reads only - nothing written.

    Split from the write so the check can run before the checkpoint's write
    transaction opens (``core/derivation_lineage.plan_operation_lineage``);
    ``withhold`` is applied inside it by ``withhold_fact``. Never raises.
    """
    try:
        rows = db.execute(
            "SELECT f.observation_date, m.speaker, m.role, m.content AS source_content "
            "FROM atomic_facts f "
            "LEFT JOIN memories m ON m.memory_id=f.memory_id "
            "WHERE f.fact_id=? AND f.profile_id=?", (fact_id, profile_id))
        row = dict(rows[0]) if rows else {}
        raw_content = row.get("source_content") or raw_content
        from superlocalmemory.core.source_excerpt import is_source_excerpt
        from superlocalmemory.encoding.speaker_spans import with_actor
        source = (raw_content if is_source_excerpt(content, raw_content) else with_actor(
            raw_content, row.get("speaker") or "", row.get("role") or "user"))
        report = check_fact_against_source(content, source,
                                           reference_date=row.get("observation_date"))
    except Exception as exc:  # pragma: no cover - defensive, pure function
        logger.warning("source fidelity check failed for %s: %s", fact_id[:16], exc)
        return REASON_PREFIX + "check_failed", True
    if report.ok:
        return None, False
    released = _released_reason(db, profile_id, fact_id)
    if released:
        return released, False  # the user judged it correct; never withhold it again
    if not WITHHOLD_REASONS & set(report.reasons):
        return FLAGGED_PREFIX + "+".join(report.reasons), False
    return REASON_PREFIX + "+".join(report.reasons), True


def is_released(db: Any, *, profile_id: str, fact_id: str) -> bool:
    """Whether the user has released this fact (read inside the write, to be sure)."""
    return _released_reason(db, profile_id, fact_id) is not None


def withhold_fact(db: Any, *, profile_id: str, fact_id: str, reason: str) -> None:
    """Take a fact out of answers (``quarantined = 1``); reversible, loses nothing."""
    if _has_quarantine_column(db):
        db.execute(
            "UPDATE atomic_facts SET quarantined = 1 "
            "WHERE fact_id = ? AND profile_id = ? AND COALESCE(quarantined, 0) = 0",
            (fact_id, profile_id),
        )
    logger.info(
        "Withheld derived fact %s: it does not match its source (%s)",
        fact_id[:16], reason[len(REASON_PREFIX):].replace("+", ","),
    )


def withhold_if_unfaithful(
    db: Any,
    *,
    profile_id: str,
    fact_id: str,
    content: str,
    raw_content: str,
) -> str | None:
    """Return the lineage reason when the fact was withheld or flagged, else None.

    Never raises: a failure to check leaves the fact as it was and is logged,
    because a fidelity check must never cost the user a write.
    """
    reason, withhold = judge_fidelity(db, profile_id=profile_id, fact_id=fact_id,
                                      content=content, raw_content=raw_content)
    if withhold and reason:
        withhold_fact(db, profile_id=profile_id, fact_id=fact_id, reason=reason)
    return reason


__all__ = ["FLAGGED_PREFIX", "REASON_PREFIX", "RELEASED_PREFIX", "WITHHOLD_REASONS",
           "is_released", "judge_fidelity", "withhold_fact", "withhold_if_unfaithful"]
