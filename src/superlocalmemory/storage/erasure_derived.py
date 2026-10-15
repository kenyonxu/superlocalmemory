# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Derived text that outlives the fact it quotes, and how erasure clears it.

Two stores keep an erased fact's words after the fact's id has left every
member list, so the read-path guard that withholds a row *while a member is
erasing* has nothing left to match once the erasure completes:

* ``persona_summary`` stitches the summaries of its communities. Those
  community rows are deleted when an erasure completes, which also removes the
  only link from the persona to the fact. The persona is a rebuildable roll-up
  (``ProgressiveAbstraction.compute_and_store`` replaces it), so it is removed
  while that link still exists and rebuilt from what remains.
* ``memory_scenes.theme`` is the first 200 characters of the scene's anchor
  fact (``encoding/scene_builder.py``). Removing the id from the scene keeps
  the theme, so the theme is recomputed from the first remaining member.

Every function takes ``execute(sql, params)`` returning an iterable of rows, so
it works on a raw ``sqlite3`` connection and on the storage manager alike.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

#: Characters of a fact that make up a scene theme (scene_builder._create_scene).
THEME_CHARS = 200

Execute = Callable[[str, tuple], Any]

#: Persona rows (per profile) built on any community that quotes the fact.
PERSONA_DELETE_SQL = (
    "DELETE FROM persona_summary WHERE profile_id = ? AND EXISTS ("
    "SELECT 1 FROM json_each(persona_summary.community_ids_json) c "
    "JOIN community_summaries cs ON cs.profile_id = persona_summary.profile_id "
    "AND cs.community_id = c.value "
    "JOIN json_each(cs.fact_ids_json) f ON f.value = ?)"
)


def _tolerant(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "malformed json" in text


def drop_personas_quoting(execute: Execute, profile_id: str, fact_id: str) -> int:
    """Delete the persona if any community it is built on quotes the fact.

    Call before the community rows are removed. Returns the rows removed.
    """
    try:
        return len(list(execute(PERSONA_DELETE_SQL + " RETURNING 1", (profile_id, fact_id))))
    except Exception as exc:  # noqa: BLE001 - tables absent on older schemas
        if _tolerant(exc):
            return 0
        raise


def _ids(raw: Any) -> list[str]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _content(row) -> str:
    """The content column of a row, whether keyed by name or by position."""
    value = row["content"] if hasattr(row, "keys") else row[0]
    return str(value or "")


def fact_content(execute: Execute, profile_id: str, fact_id: str) -> str | None:
    """The fact's text while its row still exists, else ``None``."""
    rows = list(execute("SELECT content FROM atomic_facts WHERE fact_id = ? AND profile_id = ? "
                        "LIMIT 1", (fact_id, profile_id)))
    return _content(rows[0]) if rows else None


def theme_quotes(
    theme: str, fact_ids: Sequence[str], fact_id: str, erased_text: str | None,
) -> bool:
    """Whether the scene theme is (or contains) the removed fact's words.

    The anchor is the first member, so removing it always counts, even when its
    row is already gone. Otherwise the theme must overlap the fact's text.
    """
    if not theme:
        return False
    if fact_ids and fact_ids[0] == fact_id:
        return True
    if not erased_text:
        return False
    return theme in erased_text or erased_text[:THEME_CHARS] in theme


def theme_from_members(execute: Execute, profile_id: str, member_ids: Sequence[str]) -> str:
    """The scene_builder rule applied to what remains: the first member that is
    present and not itself erasing, cut to ``THEME_CHARS``; blank if none."""
    for member in member_ids:
        text = _member_text(execute, profile_id, member)
        if text:
            return text[:THEME_CHARS]
    return ""


def _member_text(execute: Execute, profile_id: str, fact_id: str) -> str:
    base = "SELECT content FROM atomic_facts WHERE fact_id = ? AND profile_id = ?"
    guarded = (base + " AND NOT EXISTS (SELECT 1 FROM projection_tombstones pt WHERE "
               "pt.profile_id = atomic_facts.profile_id AND pt.fact_id = atomic_facts.fact_id)")
    try:
        rows = list(execute(guarded + " LIMIT 1", (fact_id, profile_id)))
    except Exception as exc:  # noqa: BLE001 - older schema without tombstones
        if "no such table" not in str(exc).lower():
            raise
        rows = list(execute(base + " LIMIT 1", (fact_id, profile_id)))
    return _content(rows[0]) if rows else ""


def retheme_scenes_quoting(execute: Execute, profile_id: str, fact_id: str) -> int:
    """At tombstone time: give every scene whose theme quotes the fact a theme
    from its remaining members, so no reader sees the words, now or after the
    id is later removed from the scene. Returns the scenes changed."""
    try:
        scenes = [tuple(r) for r in execute(
            "SELECT scene_id, theme, fact_ids_json FROM memory_scenes "
            "WHERE profile_id = ? AND fact_ids_json LIKE ?", (profile_id, f'%"{fact_id}"%'))]
    except Exception as exc:  # noqa: BLE001
        if _tolerant(exc):
            return 0
        raise
    erased_text = fact_content(execute, profile_id, fact_id)
    changed = 0
    for scene_id, theme, raw_ids in scenes:
        members = _ids(raw_ids)
        if not theme_quotes(str(theme or ""), members, fact_id, erased_text):
            continue
        rest = [m for m in members if m != fact_id]
        execute("UPDATE memory_scenes SET theme = ? WHERE scene_id = ?",
                (theme_from_members(execute, profile_id, rest), scene_id))
        changed += 1
    return changed
