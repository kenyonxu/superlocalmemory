# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Layered recall: how much of each memory a recall returns, within a budget.

``depth``
  0  glance   ids, the tier and a one-line preview (hooks use this)
  1  recall   the full facts, as recall has always returned them (the default)
  2  context  + the source memory, up to three neighbours from the same
              session, up to three facts about the same entities, and the
              correction that replaced it, when one did

``max_tokens`` caps the whole result list (estimated tokens of its JSON form)
and is never exceeded: context is dropped first, then the last memory's text
is shortened, then memories are left out -- and the response says so.

Depth 2 reads only existing tables, and every memory it adds goes through the
same gate as ``fetch``: ``get_facts_by_ids`` (profile scope, quarantine) and
the caller's visibility (hidden and remote-hidden memories never appear).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

GLANCE, RECALL, CONTEXT = 0, 1, 2
DEPTHS = (GLANCE, RECALL, CONTEXT)
PREVIEW_CHARS = 160
CONTEXT_EACH = 3
#: Below this many tokens left, a memory is left out rather than shortened.
MIN_ITEM_TOKENS = 24


def parse_depth(value: Any, default: int = RECALL) -> int:
    """A depth from a request; ValueError for anything but 0, 1 or 2."""
    if value is None or value == "":
        return default
    depth = int(value)
    if depth not in DEPTHS:
        raise ValueError("depth must be 0, 1 or 2")
    return depth


def parse_max_tokens(value: Any) -> int | None:
    if value is None or value == "" or value == 0:
        return None
    tokens = int(value)
    if tokens < 0:
        raise ValueError("max_tokens must be positive")
    return tokens


def tokens_of(value: Any) -> int:
    from superlocalmemory.core.injection import estimate_tokens

    return estimate_tokens(json.dumps(value, ensure_ascii=False, default=str))


def _one_line(text: str, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def glance(item: dict) -> dict:
    keep = ("fact_id", "memory_id", "tier", "rank_position", "score", "weak", "support")
    out = {k: item[k] for k in keep if k in item}
    out["content"] = _one_line(item.get("content", ""))
    return out


def shape(items: Sequence[dict], depth: int = RECALL, max_tokens: int | None = None,
          context_of: Callable[[dict], dict] | None = None) -> tuple[list[dict], dict]:
    """The items at ``depth`` within ``max_tokens``; ``(items, report)``."""
    if depth == GLANCE:
        shaped = [glance(i) for i in items]
    elif depth == CONTEXT and context_of is not None:
        shaped = [{**i, "context": _safe_context(context_of, i)} for i in items]
    else:
        shaped = [dict(i) for i in items]
    report = {"depth": depth, "max_tokens": max_tokens, "trimmed": False}
    if max_tokens is None:
        return shaped, report
    kept, trimmed = _fit(shaped, max_tokens)
    report.update(trimmed=trimmed, tokens=tokens_of(kept), left_out=len(shaped) - len(kept))
    return kept, report


def _safe_context(context_of: Callable[[dict], dict], item: dict) -> dict:
    try:
        return context_of(item)
    except Exception as exc:  # context is extra; its failure never loses the memory
        logger.debug("recall context: %s", exc)
        return {}


def _fit(items: list[dict], max_tokens: int) -> tuple[list[dict], bool]:
    kept: list[dict] = []
    trimmed = False
    for item in items:
        candidate = kept + [item]
        if tokens_of(candidate) <= max_tokens:
            kept = candidate
            continue
        trimmed = True
        lean = {k: v for k, v in item.items() if k != "context"}
        if tokens_of(kept + [lean]) <= max_tokens:
            kept.append(lean)
            continue
        room = max_tokens - tokens_of(kept + [{**lean, "content": ""}])
        if room < MIN_ITEM_TOKENS:
            break
        text = str(lean.get("content", ""))
        cut = text[: room * 4]
        while cut and tokens_of(kept + [{**lean, "content": cut + "…"}]) > max_tokens:
            cut = cut[: int(len(cut) * 0.85)]
        if cut:
            kept.append({**lean, "content": cut + "…"})
        break
    return kept, trimmed or len(kept) < len(items)


# -- depth 2 --------------------------------------------------------------------------

def context_for(db: Any, profile_id: str, fact_id: str, *,
                visible: Callable[[Sequence[Any]], list] | None = None) -> dict:
    """The source memory, same-session neighbours, facts about the same entities
    and the correction successor of one fact, each through the fetch gate."""
    facts = db.get_facts_by_ids([fact_id], profile_id)
    if not facts:
        return {}
    fact = facts[0]
    source = db.get_memory_content_batch([fact.memory_id], profile_id).get(fact.memory_id, "")
    ids = {"session": _session_ids(db, profile_id, fact),
           "entities": _entity_ids(db, profile_id, fact),
           "successor": _successor_ids(db, profile_id, fact_id)}
    wanted = list(dict.fromkeys(i for group in ids.values() for i in group))
    found = db.get_facts_by_ids(wanted, profile_id) if wanted else []
    found = _visible(db, profile_id, visible(found) if visible else found)
    by_id = {f.fact_id: f for f in found}

    def rows(group: str) -> list[dict]:
        return [{"fact_id": i, "memory_id": by_id[i].memory_id,
                 "content": _one_line(by_id[i].content, 240)}
                for i in ids[group] if i in by_id][:CONTEXT_EACH]
    successor = rows("successor")
    return {"source": source, "same_session": rows("session"),
            "same_entities": rows("entities"),
            "superseded_by": successor[0] if successor else None}


def _visible(db: Any, profile_id: str, facts: list) -> list:
    from superlocalmemory.retrieval import visibility

    if visibility.is_empty():
        return facts
    hidden = visibility.hidden_among(db, profile_id, [f.fact_id for f in facts])
    return [f for f in facts if f.fact_id not in hidden]


def _ids(rows: Sequence[Any], key: str = "fact_id") -> list[str]:
    return [str(dict(r)[key]) for r in rows]


def _session_ids(db: Any, profile_id: str, fact: Any) -> list[str]:
    session = getattr(fact, "session_id", "") or ""
    if not session:
        return []
    return _ids(db.execute(
        "SELECT fact_id FROM atomic_facts WHERE profile_id = ? AND session_id = ? "
        "AND fact_id != ? AND memory_id != ? ORDER BY created_at DESC LIMIT ?",
        (profile_id, session, fact.fact_id, fact.memory_id, CONTEXT_EACH * 2)))


def _entity_ids(db: Any, profile_id: str, fact: Any) -> list[str]:
    out: list[str] = []
    for entity in list(getattr(fact, "canonical_entities", None) or ())[:CONTEXT_EACH]:
        out += _ids(db.execute(
            "SELECT fact_id FROM atomic_facts WHERE profile_id = ? AND fact_id != ? "
            "AND memory_id != ? AND canonical_entities_json LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (profile_id, fact.fact_id, fact.memory_id,
             "%" + json.dumps(str(entity)) + "%", CONTEXT_EACH)))
    return list(dict.fromkeys(out))


def _successor_ids(db: Any, profile_id: str, fact_id: str) -> list[str]:
    try:
        return _ids(db.execute(
            "SELECT successor_fact_id FROM correction_cases WHERE profile_id = ? "
            "AND predecessor_fact_id = ? AND status = 'applied' "
            "ORDER BY applied_at DESC LIMIT 1", (profile_id, fact_id)),
            "successor_fact_id")
    except Exception:  # a store from before corrections were recorded
        return []


__all__ = ["CONTEXT", "DEPTHS", "GLANCE", "RECALL", "context_for", "glance", "parse_depth",
           "parse_max_tokens", "shape", "tokens_of"]
