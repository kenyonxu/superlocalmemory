# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""``slm db repair-entities``: undo the entity links the old fuzzy merge chained.

WHY. Before 4.1.25 the resolver merged a new name into any entity whose name
OR ALIAS it matched by whole-string Jaro-Winkler >= 0.85, and wrote the new
name as an alias the next name could match. On the author's 26k-fact store
about half of all fact/entity links pointed at the wrong entity, one entity
("Lost") held 145 aliases, and about 42% of entity edges joined facts that
share no name. The resolver no longer does this (encoding/entity_match); this
repairs what it already wrote, by the same rules:

1. ``aliases`` -- every alias other than an entity's own name and those the
   4.1.25 rules wrote is checked against its entity's own name: the old fuzzy
   merge's (``jaro_winkler``), the old model mapping's (``llm``) and old
   entity merges' (``merge``...). One the rules call the same name is kept and
   relabelled with the rule that justifies it; a model's or merge's alias the
   rules call borderline is kept; any other is removed.
2. ``links`` -- each fact that has entity links is linked again from the names
   the current extractor finds in its text, under the validity rules
   (encoding/entity_validity): no ids, dates, paths, file names, status
   words, everyday words where a sentence starts, or fragments. Each name
   resolves by exact name, a kept alias, the same name normalized, a one-word
   typo -- never to an entity whose own name fails those rules; a name that
   matches none becomes its own entity. A fact with no links is left alone:
   this repairs links, it does not enrich.
3. ``index`` -- ``fact_entity_associations`` follows the new links, and each
   touched entity's ``fact_count`` is recounted.
4. ``edges`` -- an entity, temporal or causal edge (each built only between
   facts sharing an entity) touching a relinked fact is removed when its two
   facts no longer share one.
5. ``events`` -- a dated fact's temporal event that named an entity the fact
   no longer links to is pointed at one it does (or the per-profile
   placeholder an entity-less dated fact uses), so the fact stays reachable
   by date.
6. ``junk`` -- an entity no fact links to after the repair is removed with its
   aliases when the repair emptied it, or when its name fails the validity
   rules; one still named by a summary, profile, scene, community or temporal
   event is kept and counted (storage/entity_link_repair_junk).

``plan`` reads and changes nothing. ``entity_link_repair_apply`` writes an
undo record first, then every change, in one transaction; a second run finds
nothing to do.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from superlocalmemory.encoding import entity_match as em
from superlocalmemory.encoding.entity_mentions import extract_mentions
from superlocalmemory.encoding.entity_resolver import _usable_mention
from superlocalmemory.encoding.entity_validity import valid_name

#: Edge types that exist only because the two facts shared an entity.
ENTITY_EDGE_TYPES = ("entity", "temporal", "causal")
PLACEHOLDER_PREFIX = "__slm_unresolved__:"


@dataclass(frozen=True, slots=True)
class AliasFix:
    alias_id: str
    profile_id: str
    entity_id: str
    alias: str
    confidence: float
    source: str
    new_source: str | None  # None: remove
    reason: str


@dataclass(frozen=True, slots=True)
class FactFix:
    fact_id: str
    profile_id: str
    old_json: str
    new_ids: tuple[str, ...]  # may name keys of ``new_entities`` ("new:<key>")


@dataclass
class ProfilePlan:
    profile_id: str
    aliases: list[AliasFix] = field(default_factory=list)
    facts: list[FactFix] = field(default_factory=list)
    new_entities: dict[str, str] = field(default_factory=dict)  # key -> name
    links: dict[str, tuple[str, ...]] = field(default_factory=dict)  # every fact
    junk: list[str] = field(default_factory=list)  # entity ids to remove
    junk_kept_referenced: int = 0


@dataclass
class EntityRepairPlan:
    profiles: list[ProfilePlan]

    def summary(self) -> dict[str, int]:
        aliases = [a for p in self.profiles for a in p.aliases]
        facts = [f for p in self.profiles for f in p.facts]
        return {
            "aliases_removed": sum(1 for a in aliases if a.new_source is None),
            "aliases_kept_relabelled": sum(1 for a in aliases if a.new_source),
            "facts_relinked": len(facts),
            "facts_left_with_no_entity": sum(1 for f in facts if not f.new_ids),
            "entities_to_create": sum(len(p.new_entities) for p in self.profiles),
            "junk_entities_to_remove": sum(len(p.junk) for p in self.profiles),
            "junk_entities_kept_referenced": sum(p.junk_kept_referenced
                                                 for p in self.profiles),
        }

    @property
    def empty(self) -> bool:
        return not any(p.aliases or p.facts or p.junk for p in self.profiles)


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    return list(conn.execute(sql, args).fetchall())


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


#: Alias sources the 4.1.25 rules write; they are already justified.
CURRENT_SOURCES = frozenset({"normalized", "typo", "llm_confirmed"})


def _alias_fix(row: tuple, profile_id: str, canonical: str) -> AliasFix | None:
    alias_id, entity_id, alias, confidence, source = row
    source = str(source or "")
    if source in CURRENT_SOURCES or em.name_key(str(alias)).compact == em.name_key(
            canonical).compact:
        return None
    verdict, reason = em.compare(str(alias), canonical)
    if verdict == em.BORDERLINE and source != "jaro_winkler":
        if source != "llm":
            return None  # an old merge's borderline alias: left as it is
        new_source: str | None = "llm_confirmed"  # left to a model, and one judged it
    else:
        new_source = reason if verdict == em.SAME else None
    return AliasFix(str(alias_id), profile_id, str(entity_id), str(alias),
                    float(confidence or 0.0), source, new_source, reason)


def _plan_aliases(conn: sqlite3.Connection, plan: ProfilePlan, names: dict[str, str],
                  skip: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Alias decisions; returns the aliases a lookup may use, lower -> ids.

    An alias id in ``skip`` (an earlier run the user undid) is left as it is."""
    usable: dict[str, list[str]] = {}
    for row in _rows(conn, "SELECT alias_id, entity_id, alias, confidence, source "
                           "FROM entity_aliases WHERE profile_id = ? ORDER BY rowid",
                     (plan.profile_id,)):
        canonical = names.get(str(row[1]))
        if canonical is None:
            continue
        fix = _alias_fix(row, plan.profile_id, canonical)
        if fix is not None and fix.alias_id in skip:
            fix = None
        if fix is not None:
            plan.aliases.append(fix)
            if fix.new_source is None:
                continue
        usable.setdefault(str(row[2]).lower(), []).append(str(row[1]))
    return usable


class _Linker:
    """Resolves a fact's raw names by the save rules, read-only."""

    def __init__(self, entities: list[tuple[str, str]], aliases: dict[str, list[str]],
                 plan: ProfilePlan) -> None:
        # Never link to an entity whose own name fails the validity rules
        # ("/api/*" collected 718 facts through an alias "API").
        entities = [(e, n) for e, n in entities if valid_name(n, capital_evidence=True)]
        good = {e for e, _n in entities}
        self._exact: dict[str, list[str]] = {}
        for entity_id, name in entities:
            self._exact.setdefault(name.lower(), []).append(entity_id)
        self._aliases = {k: [e for e in ids if e in good] for k, ids in aliases.items()}
        self._index = em.NameIndex(entities)
        self._plan = plan

    def link(self, raw: str, old: tuple[str, ...]) -> str | None:
        name = _usable_mention(raw)
        if name is None:
            return None
        for ids in (self._exact.get(name.lower()), self._aliases.get(name.lower())):
            if ids:
                return next((i for i in ids if i in old), ids[0])
        verdict, entity_id, _c, _w = self._index.best(name)
        if verdict == em.SAME and entity_id:
            return entity_id
        key = em.name_key(name).compact
        if not key:
            return None
        self._plan.new_entities.setdefault(key, name)
        return f"new:{key}"


def _decode(raw: object) -> list[str]:
    try:
        values = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return [str(v) for v in values if v] if isinstance(values, list) else []


def fact_names(content: str) -> list[str]:
    """The names a fact is linked by: what the current extractor finds in its
    text (validity rules included), one per spelling.

    The names saved with the fact are not used: on the author's store they
    came from the old extractor and from a model, and relinking from them
    recreated "Users", "LIVE", 16-hex ids and quoted questions as entities.
    Re-extracting also links each fact exactly as a new save of it would be.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in extract_mentions(content or ""):
        key = em.name_key(name).compact
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _plan_facts(conn: sqlite3.Connection, plan: ProfilePlan, linker: _Linker,
                skip: frozenset[str] = frozenset()) -> None:
    for fact_id, content, links_json in _rows(
            conn, "SELECT fact_id, content, canonical_entities_json "
                  "FROM atomic_facts WHERE profile_id = ? ORDER BY rowid", (plan.profile_id,)):
        old = tuple(dict.fromkeys(_decode(links_json)))
        plan.links[str(fact_id)] = old
        if not old or str(fact_id) in skip:
            continue
        names = fact_names(str(content or ""))
        new = tuple(dict.fromkeys(i for i in (linker.link(n, old) for n in names) if i))
        if new != old:
            plan.facts.append(FactFix(str(fact_id), plan.profile_id, str(links_json), new))
            plan.links[str(fact_id)] = new


def plan_profile(conn: sqlite3.Connection, profile_id: str,
                 skip: frozenset[str] = frozenset()) -> ProfilePlan:
    plan = ProfilePlan(profile_id)
    entities = [(str(e), str(n or "")) for e, n in _rows(
        conn, "SELECT entity_id, canonical_name FROM canonical_entities "
              "WHERE profile_id = ? AND entity_id NOT LIKE ? ORDER BY rowid",
        (profile_id, PLACEHOLDER_PREFIX + "%"))]
    names = dict(entities)
    aliases = _plan_aliases(conn, plan, names, skip)
    _plan_facts(conn, plan, _Linker(entities, aliases, plan), skip)
    used = {i for f in plan.facts for i in f.new_ids}
    plan.new_entities = {k: v for k, v in plan.new_entities.items() if f"new:{k}" in used}
    from superlocalmemory.storage.entity_link_repair_junk import plan_junk

    plan_junk(conn, plan, [(e, n) for e, n in entities if e not in skip])
    return plan


def plan(conn: sqlite3.Connection, skip: frozenset[str] = frozenset()) -> EntityRepairPlan:
    """What a repair would change, read-only.

    ``skip`` holds ids (facts, aliases, entities) an undone run touched: they
    are left exactly as they are, so an undo is final until the user asks again.
    """
    if not _has_column(conn, "atomic_facts", "entities_json"):
        return EntityRepairPlan([])
    profiles = [str(r[0]) for r in _rows(
        conn, "SELECT DISTINCT profile_id FROM canonical_entities ORDER BY profile_id")]
    return EntityRepairPlan([plan_profile(conn, p, skip) for p in profiles])


def report(conn: sqlite3.Connection, the_plan: EntityRepairPlan, sample: int = 8) -> dict:
    """Counts plus a few examples (names only), for the preview."""
    from superlocalmemory.storage.entity_link_repair_apply import count_effects

    out: dict = {**the_plan.summary(), **count_effects(conn, the_plan)}
    removed = [a for p in the_plan.profiles for a in p.aliases if a.new_source is None]
    by_entity: dict[str, int] = {}
    for a in removed:
        by_entity[a.entity_id] = by_entity.get(a.entity_id, 0) + 1
    worst = sorted(by_entity.items(), key=lambda kv: -kv[1])[:sample]
    names = {str(e): str(n) for e, n in _rows(conn, "SELECT entity_id, canonical_name "
                                                    "FROM canonical_entities")}
    out["most_aliases_removed"] = [{"entity": names.get(e, e), "aliases_removed": n}
                                   for e, n in worst]
    out["example_aliases_removed"] = [f"{a.alias} -/-> {names.get(a.entity_id, '?')}"
                                      for a in removed[:sample]]
    return out


__all__ = ["ENTITY_EDGE_TYPES", "AliasFix", "EntityRepairPlan", "FactFix", "ProfilePlan",
           "CURRENT_SOURCES", "fact_names", "plan", "plan_profile", "report"]
