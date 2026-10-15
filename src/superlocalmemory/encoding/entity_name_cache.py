# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""A profile's canonical names, kept between saves for the merge rules.

The rules in ``entity_match`` compare a new name with every canonical name
that starts with the same letter. Reading and keying every name for each save
cost ~120 ms on a 15k-entity profile; kept here it is paid once. The kept
names are reused while the profile's entity count and highest rowid are what
they were after the last change this process made, and a rule's hit is checked
against the table before it is used, so a stale copy can only miss a match
(the name becomes its own entity), never link a fact to a removed entity.
"""

from __future__ import annotations

import threading
from typing import Any

from superlocalmemory.encoding import entity_match as em

_STAMP_SQL = ("SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS r "
              "FROM canonical_entities WHERE profile_id = ?")
_NAMES_SQL = ("SELECT entity_id, canonical_name FROM canonical_entities "
              "WHERE profile_id = ? ORDER BY rowid")
_ADDED_SQL = ("SELECT entity_id, canonical_name FROM canonical_entities "
              "WHERE profile_id = ? AND rowid > ? ORDER BY rowid")


class CanonicalNames:
    """Per-profile ``NameIndex`` over canonical names (never aliases)."""

    def __init__(self, db: Any, max_profiles: int = 8) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._max = max_profiles
        self._kept: dict[str, tuple[tuple[int, int], em.NameIndex]] = {}

    def _stamp(self, profile_id: str) -> tuple[int, int]:
        row = dict(self._db.execute(_STAMP_SQL, (profile_id,))[0])
        return int(row["n"] or 0), int(row["r"] or 0)

    def index(self, profile_id: str) -> em.NameIndex:
        stamp = self._stamp(profile_id)
        with self._lock:
            kept = self._kept.get(profile_id)
        if kept is not None and kept[0] == stamp:
            return kept[1]
        if kept is not None and self._grow(profile_id, kept, stamp):
            return kept[1]
        names = em.NameIndex([(str(dict(r)["entity_id"]), str(dict(r)["canonical_name"] or ""))
                              for r in self._db.execute(_NAMES_SQL, (profile_id,))])
        with self._lock:
            self._kept[profile_id] = (stamp, names)
            while len(self._kept) > self._max:
                self._kept.pop(next(iter(self._kept)))
        return names

    def _grow(self, profile_id: str, kept, stamp: tuple[int, int]) -> bool:
        """Fold in names only added since ``kept`` (4.2.4).

        A save that created one entity made the next recall rebuild all of a
        profile's names: 2.7-5.4 s on a 16,800-entity store. Added rows are
        read by rowid; a removal (count lower than the rows added) rebuilds.
        """
        (n0, r0), names = kept
        n1, r1 = stamp
        if n1 < n0 or r1 < r0:
            return False
        added = [dict(r) for r in self._db.execute(_ADDED_SQL, (profile_id, r0))]
        if len(added) != n1 - n0:
            return False
        for row in added:
            names.add(str(row["entity_id"]), str(row["canonical_name"] or ""))
        with self._lock:
            self._kept[profile_id] = (stamp, names)
        return True

    def added(self, profile_id: str, entity_id: str, name: str) -> None:
        """This process created an entity: keep the copy current."""
        with self._lock:
            kept = self._kept.get(profile_id)
        if kept is None:
            return
        kept[1].add(entity_id, name)
        stamp = self._stamp(profile_id)
        with self._lock:
            self._kept[profile_id] = (stamp, kept[1])

    def best(self, name: str, profile_id: str) -> tuple[str, str | None, str, str]:
        """``NameIndex.best``, with a hit confirmed against the table."""
        verdict, entity_id, canonical, why = self.index(profile_id).best(name)
        if entity_id is None or verdict == em.DIFFERENT:
            return verdict, entity_id, canonical, why
        if self._db.execute("SELECT 1 FROM canonical_entities WHERE entity_id = ? "
                            "AND profile_id = ?", (entity_id, profile_id)):
            return verdict, entity_id, canonical, why
        with self._lock:
            self._kept.pop(profile_id, None)
        return em.DIFFERENT, None, "", "removed since"


__all__ = ["CanonicalNames"]
