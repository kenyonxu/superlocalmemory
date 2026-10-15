# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Recall's read-only entity name lookup, without scanning the tables each time.

``EntityResolver.lookup`` (recall only; never saving) found each name in a
question by an exact match, then an alias, then the closest spelling, and each
step scanned a whole table: ``LOWER(canonical_name) = LOWER(?)`` has no index,
and the spelling match read every name and alias and scored them all. On a
22k-fact store that was 46 ms of a recall, every recall.

This keeps those rows in memory per profile while nothing has been committed
to the store (storage/store_signature) and answers the same way:

* exact name and alias keys are lowered the way SQLite's ``LOWER`` does (ASCII
  only), so a name matches here exactly when it matched there;
* a key held by more than one entity is answered by the original query, so
  which row wins never depends on a query plan;
* the spelling match applies a save's merge rules (``entity_match``) to the
  canonical names only (``entity_name_cache``); its answer per name is kept
  while the rows are.

Aliases written by the pre-4.1.25 whole-string fuzzy merge are not loaded:
they may be links of a chain ("Plorth" -> "Plot" -> "Lost").
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from superlocalmemory.encoding import entity_match as em
from superlocalmemory.storage import store_signature

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

#: The original queries the spelling match ran; their row order is reused.
_NAMES_SQL = ("SELECT entity_id, canonical_name, entity_type FROM canonical_entities "
              "WHERE profile_id = ?")
_ALIASES_SQL = ("SELECT ea.entity_id, ea.alias, ea.source, ce.canonical_name "
                "FROM entity_aliases ea "
                "JOIN canonical_entities ce ON ce.entity_id = ea.entity_id "
                "WHERE ce.profile_id = ?")

AMBIGUOUS = object()


def sql_lower(text: str) -> str:
    """SQLite's built-in LOWER(): ASCII letters only."""
    return text.translate(_ASCII_LOWER)


def _keyed(rows: list[tuple[str, str]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for entity_id, text in rows:
        key = sql_lower(text)
        prev = out.get(key)
        if prev is None:
            out[key] = entity_id
        elif prev is not AMBIGUOUS and prev != entity_id:
            out[key] = AMBIGUOUS
    return out


@dataclass
class _Index:
    names: list[tuple[str, str]]
    aliases: list[tuple[str, str]]
    by_name: dict[str, object]
    by_alias: dict[str, object]
    types: dict[str, str] = field(default_factory=dict)
    same: dict[str, str | None] = field(default_factory=dict)
    name_of: dict[str, str] | None = None


#: The entity rows this index reads: a profile's names, and every alias (an
#: alias row's own profile can differ from its entity's). A save that adds or
#: removes one moves its count or highest rowid; touching last_seen or
#: fact_count, which every save does, moves neither (4.2.4).
_NAMES_STAMP_SQL = ("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS r "
                    "FROM canonical_entities WHERE profile_id = ?")
_ALIASES_STAMP_SQL = "SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS r FROM entity_aliases"
_NEW_NAMES_SQL = ("SELECT rowid AS rid, entity_id, canonical_name, entity_type "
                  "FROM canonical_entities WHERE profile_id = ? AND rowid > ? ORDER BY rowid")
_NEW_ALIASES_SQL = ("SELECT ea.rowid AS rid, ea.entity_id, ea.alias, ea.source, ce.canonical_name "
                    "FROM entity_aliases ea "
                    "JOIN canonical_entities ce ON ce.entity_id = ea.entity_id "
                    "WHERE ce.profile_id = ? AND ea.rowid > ? ORDER BY ea.rowid")
#: An in-place rename or alias repair moves no count. Maintenance that does one
#: calls ``invalidate()``; this bounds anything else (another process) too.
MAX_AGE_S = 600.0

_generation = 0
_generation_lock = threading.Lock()


def invalidate() -> None:
    """Entity names or aliases were changed in place: rebuild on next use."""
    global _generation
    with _generation_lock:
        _generation += 1


def _stamp(db: Any, profile_id: str) -> tuple[int, int, int, int]:
    names = dict(db.execute(_NAMES_STAMP_SQL, (profile_id,))[0])
    aliases = dict(db.execute(_ALIASES_STAMP_SQL)[0])
    return (int(names["n"] or 0), int(names["r"] or 0),
            int(aliases["n"] or 0), int(aliases["r"] or 0))


def _add_keyed(keyed: dict[str, object], rows: list[tuple[str, str]]) -> None:
    for entity_id, text in rows:
        key = sql_lower(text)
        prev = keyed.get(key)
        if prev is None:
            keyed[key] = entity_id
        elif prev is not AMBIGUOUS and prev != entity_id:
            keyed[key] = AMBIGUOUS


class EntityLookupIndex:
    """Per-profile name rows, kept while the entity rows they come from are.

    Keyed on the entity tables (``_stamp``), not on the whole store: before
    4.2.4 any commit -- a hook's note, an access count -- dropped it, so on a
    busy store it was rebuilt on nearly every recall (16,800 names re-keyed,
    ~0.35 s). Rows only added since are folded in; anything else rebuilds.
    """

    def __init__(self, db: Any, *, clock=time.monotonic) -> None:
        self._db = db
        self._clock = clock
        self._lock = threading.Lock()
        # profile -> (stamp, generation, built_at, index)
        self._parts: dict[str, tuple[tuple, int, float, _Index]] = {}

    def get(self, profile_id: str) -> _Index | None:
        if store_signature.of_db(self._db) is None:  # inside a write: never reuse
            return None
        stamp = _stamp(self._db, profile_id)
        now = self._clock()
        with self._lock:
            hit = self._parts.get(profile_id)
        if hit is not None and hit[1] == _generation and now - hit[2] < MAX_AGE_S:
            if hit[0] == stamp:
                return hit[3]
            grown = self._extend(profile_id, hit[0], stamp, hit[3])
            if grown is not None:
                self._keep(profile_id, (stamp, hit[1], hit[2], grown))
                return grown
        generation = _generation
        index = self._build(profile_id)
        self._keep(profile_id, (stamp, generation, now, index))
        return index

    def _keep(self, profile_id: str, entry: tuple) -> None:
        with self._lock:
            self._parts[profile_id] = entry
            while len(self._parts) > 8:  # a few profiles at most
                self._parts.pop(next(iter(self._parts)))

    def _build(self, profile_id: str) -> _Index:
        rows = [dict(r) for r in self._db.execute(_NAMES_SQL, (profile_id,))]
        names = [(str(r["entity_id"]), str(r["canonical_name"] or "")) for r in rows]
        types = {str(r["entity_id"]): str(r["entity_type"] or "") for r in rows}
        aliases = [(str(r["entity_id"]), str(r["alias"] or ""))
                   for r in map(dict, self._db.execute(_ALIASES_SQL, (profile_id,)))
                   if em.alias_in_use(r["source"], r["alias"], r["canonical_name"])]
        return _Index(names, aliases, _keyed(names), _keyed(aliases), types)

    def _extend(self, profile_id: str, old: tuple, new: tuple, index: _Index) -> _Index | None:
        """The index plus rows added since ``old``; None when any were removed."""
        n0, r0, a0, ar0 = old
        n1, r1, a1, ar1 = new
        if n1 < n0 or r1 < r0 or a1 < a0 or ar1 < ar0:
            return None
        name_rows = [dict(r) for r in self._db.execute(_NEW_NAMES_SQL, (profile_id, r0))]
        alias_rows = [dict(r) for r in self._db.execute(_NEW_ALIASES_SQL, (profile_id, ar0))]
        if len(name_rows) != n1 - n0:
            return None  # a removal hidden by an addition: rebuild
        if a1 - a0 < len(alias_rows):
            return None
        names = [(str(r["entity_id"]), str(r["canonical_name"] or "")) for r in name_rows]
        aliases = [(str(r["entity_id"]), str(r["alias"] or "")) for r in alias_rows
                   if em.alias_in_use(r["source"], r["alias"], r["canonical_name"])]
        by_name, by_alias = dict(index.by_name), dict(index.by_alias)
        _add_keyed(by_name, names)
        _add_keyed(by_alias, aliases)
        types = dict(index.types)
        types.update({str(r["entity_id"]): str(r["entity_type"] or "") for r in name_rows})
        # A new name can be what an earlier unmatched spelling meant: forget those.
        return _Index(index.names + names, index.aliases + aliases, by_name, by_alias, types)


def plain_word_hit(index: _Index, word: str) -> str | None:
    """The entity a lower-case question word spells exactly (as SQLite's
    LOWER does), when such a word may name it at all
    (``entity_match.plain_word_may_name``). An ambiguous spelling answers none.
    """
    key = sql_lower(word)
    hit = index.by_name.get(key)
    if hit is None:
        hit = index.by_alias.get(key)
    if hit is None or hit is AMBIGUOUS:
        return None
    if index.name_of is None:
        index.name_of = dict(index.names)
    entity_id = str(hit)
    canonical = index.name_of.get(entity_id, "")
    ok = em.plain_word_may_name(word, canonical, index.types.get(entity_id, ""))
    return entity_id if ok else None


__all__ = ["AMBIGUOUS", "EntityLookupIndex", "invalidate", "plain_word_hit", "sql_lower"]
