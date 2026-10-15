# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The standing rules and decisions a session starts with.

Only kinds a person or the saving agent confirmed are read (LLD invariant I6):
a model's suggestion never decides what every session is told. A fact that was
archived, quarantined or marked replaced is left out - a replaced decision is
exactly what must not be loaded. A rule matters whether or not it was read
recently, so warm and cold facts count; only archived ones do not.

Reads only. Returns [] on a store without the kind columns, or on any error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from superlocalmemory.storage.memory_kinds import CONFIRMED_SOURCES, MemoryKind

logger = logging.getLogger(__name__)

#: Caps per session: enough to state a working agreement, small enough that
#: the injected block stays readable.
MAX_RULES = 10
MAX_DECISIONS = 5


@dataclass(frozen=True, slots=True)
class StandingFact:
    fact_id: str
    content: str
    kind: str
    importance: float
    access_count: int


def _has_table(db: Any, name: str) -> bool:
    rows = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return bool(rows)


def confirmed_facts(
    db: Any, profile_id: str, kind: MemoryKind, limit: int, project_path: str = ""
) -> list[StandingFact]:
    """Current confirmed facts; filter project identity before applying the session cap."""
    try:
        if limit <= 0 or not db.has_memory_kind_columns():
            return []
        rows = _confirmed_rows(db, profile_id, kind, limit, project_path)
        rows = _project_rows(list(rows), project_path)
        candidate_ids = [str(dict(row)["fact_id"]) for row in rows]
        withheld = (
            db.get_nonapplied_correction_successor_ids(candidate_ids, profile_id)
            if candidate_ids
            else set()
        )
        if not isinstance(withheld, set):
            logger.debug("Standing %s lookup skipped: malformed admission result", kind.value)
            return []
    except Exception as exc:  # noqa: BLE001 - session start must never fail on this
        logger.debug("Standing %s lookup skipped: %s", kind.value, type(exc).__name__)
        return []
    out = []
    for row in rows:
        d = dict(row)
        fact_id = str(d["fact_id"])
        if fact_id in withheld:
            continue
        out.append(
            StandingFact(
                fact_id,
                str(d["content"] or ""),
                kind.value,
                float(d.get("importance") or 0.0),
                int(d.get("access_count") or 0),
            )
        )
    return out[:limit]


def standing_facts(
    db: Any, profile_id: str, exclude: frozenset[str] = frozenset(), *, project_path: str = ""
) -> list[StandingFact]:
    """Standing rules first, then active decisions; ``exclude`` (already shown,
    e.g. pinned) does not use up a slot."""
    out: list[StandingFact] = []
    for kind, cap in ((MemoryKind.RULE, MAX_RULES), (MemoryKind.DECISION, MAX_DECISIONS)):
        found = confirmed_facts(db, profile_id, kind, cap + len(exclude), project_path)
        out.extend([f for f in found if f.fact_id not in exclude][:cap])
    return out


def enabled(config: Any) -> bool:
    """``memory_kinds.standing_rules_in_session`` (default on)."""
    section = getattr(config, "memory_kinds", None)
    value = getattr(section, "standing_rules_in_session", True)
    return value is not False


__all__ = [
    "MAX_DECISIONS",
    "MAX_RULES",
    "StandingFact",
    "confirmed_facts",
    "enabled",
    "standing_facts",
]


def _project_rows(rows: list, project_path: str) -> list:
    from superlocalmemory.core.project_identity import project_key

    if not project_path:
        return rows
    wanted = project_key(project_path)
    admitted = []
    for row in rows:
        try:
            meta = json.loads(dict(row).get("metadata_json") or "{}")
            project = project_key(meta.get("project") or meta.get("project_path"))
            if project is None or project == wanted:
                admitted.append(row)
        except (ValueError, TypeError, AttributeError):
            continue
    return admitted


def _confirmed_rows(db, profile_id, kind, limit, project_path):
    sources = sorted(s.value for s in CONFIRMED_SOURCES)
    replaced = ""
    if _has_table(db, "fact_temporal_validity"):
        replaced = (
            " AND NOT EXISTS (SELECT 1 FROM fact_temporal_validity v "
            "WHERE v.fact_id = f.fact_id AND v.system_expired_at IS NOT NULL)"
        )
    return db.execute(
        "SELECT f.fact_id, f.content, f.importance, f.access_count, m.metadata_json "
        "FROM atomic_facts f LEFT JOIN memories m ON m.memory_id=f.memory_id "
        "AND m.profile_id=f.profile_id "
        "WHERE f.profile_id = ? AND f.memory_kind = ? "
        f"AND f.memory_kind_source IN ({','.join('?' * len(sources))}) "
        "AND COALESCE(f.lifecycle, 'active') != 'archived' "
        "AND COALESCE(f.quarantined, 0) = 0"
        + replaced
        + " ORDER BY f.created_at DESC"
        + ("" if project_path else " LIMIT ?"),
        (profile_id, kind.value, *sources) + (() if project_path else (int(limit),)),
    )
