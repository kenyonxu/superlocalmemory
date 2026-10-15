# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""Apply and undo an entity link repair (plan: ``entity_link_repair``).

``apply`` holds the process write lock for one transaction: it plans again
inside it (so it acts on what is there, not on an old preview), writes the
undo record, then every change. ``undo`` puts back, for one run, every row
that run removed or changed, and removes the entities it created when nothing
links to them any more. A row changed since the run is left as it is and
counted. The undo record carries ``profile_id`` on every row, so erasing a
profile erases its undo record too.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from superlocalmemory.storage import entity_link_repair as elr
from superlocalmemory.storage.entity_link_repair_junk import (
    remove_junk,
    restore_junk,
    restore_lists,
    unproject,
)

RUNS = "entity_link_repair_runs"
UNDO = "entity_link_repair_undo"
_CHUNK = 200
_IN = 900  # bound parameters per IN (...) list

_DDL = f"""
CREATE TABLE IF NOT EXISTS {RUNS} (
    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, summary_json TEXT NOT NULL,
    undone_at TEXT
);
CREATE TABLE IF NOT EXISTS {UNDO} (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, profile_id TEXT NOT NULL,
    kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (run_id, seq)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _chunks(items: list, size: int = _IN):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (name,)).fetchone())


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _select(conn: sqlite3.Connection, table: str, where: str, ids: list[str],
            extra: tuple = ()) -> list[dict]:
    """Rows of ``table`` matching ``where``; each ``{marks}`` is an IN list of ids."""
    cols = _cols(conn, table)
    out: list[dict] = []
    for part in _chunks(ids):
        clause = where.format(marks=",".join("?" * len(part)))
        params = (*extra, *(part * where.count("{marks}")))
        out += [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {','.join(cols)} FROM {table} WHERE {clause}", params)]
    return out


def _edges_to_drop(conn: sqlite3.Connection, p: elr.ProfilePlan,
                   ids: dict[str, str]) -> list[dict]:
    """Shared-entity edges touching a relinked fact whose facts now share none.

    One pass over the profile's entity-made edges (ids only), then the full
    rows of those to drop: an ``IN`` list over source OR target scanned the
    whole table once per chunk (222 s on the author's 616k edges)."""
    if not _table(conn, "graph_edges") or not p.facts:
        return []
    links = {f: {ids.get(i, i) for i in v} for f, v in p.links.items()}
    changed = {f.fact_id for f in p.facts}
    types = ",".join(f"'{t}'" for t in elr.ENTITY_EDGE_TYPES)
    drop: list[str] = []
    for edge_id, src, dst in conn.execute(
            f"SELECT edge_id, source_id, target_id FROM graph_edges WHERE profile_id = ? "
            f"AND edge_type IN ({types})", (p.profile_id,)):
        if src not in changed and dst not in changed:
            continue
        a, b = links.get(src), links.get(dst)
        if a is not None and b is not None and not (a & b):
            drop.append(edge_id)
    return _select(conn, "graph_edges", "edge_id IN ({marks})", drop)


def _events_to_move(conn: sqlite3.Connection, p: elr.ProfilePlan,
                    ids: dict[str, str]) -> list[tuple[str, str, str]]:
    """(event_id, old entity, new entity) for events naming an unlinked entity."""
    if not _table(conn, "temporal_events") or not p.facts:
        return []
    new = {f.fact_id: [ids.get(i, i) for i in f.new_ids] for f in p.facts}
    rows = _select(conn, "temporal_events", "fact_id IN ({marks})", list(new))
    covered: dict[str, set[str]] = {}
    for e in rows:
        covered.setdefault(e["fact_id"], set()).add(e["entity_id"])
    moves = []
    for e in rows:
        fact, old = e["fact_id"], e["entity_id"]
        if old in new[fact] or old.startswith(elr.PLACEHOLDER_PREFIX):
            continue
        free = [i for i in new[fact] if i not in covered[fact]]
        target = free[0] if free else f"{elr.PLACEHOLDER_PREFIX}{p.profile_id}"
        covered[fact].add(target)
        moves.append((e["event_id"], old, target))
    return moves


def count_effects(conn: sqlite3.Connection, the_plan: elr.EntityRepairPlan) -> dict:
    """Edges and events a run would change (new entities by placeholder id)."""
    edges = events = 0
    for p in the_plan.profiles:
        ids = {f"new:{k}": f"new:{k}" for k in p.new_entities}
        edges += len(_edges_to_drop(conn, p, ids))
        events += len(_events_to_move(conn, p, ids))
    return {"edges_removed": edges, "temporal_events_repointed": events}


class _Undo:
    def __init__(self, conn: sqlite3.Connection, run_id: str) -> None:
        self.conn, self.run_id, self.seq = conn, run_id, 0

    def put(self, profile_id: str, kind: str, items: list) -> None:
        for part in _chunks(items, _CHUNK):
            self.seq += 1
            self.conn.execute(f"INSERT INTO {UNDO} VALUES (?,?,?,?,?)",
                              (self.run_id, self.seq, profile_id, kind,
                               json.dumps(part, default=str)))


def _create_entities(conn: sqlite3.Connection, p: elr.ProfilePlan) -> dict[str, str]:
    from superlocalmemory.encoding.entity_resolver import _guess_entity_type

    ids: dict[str, str] = {}
    now = _now()
    for key, name in p.new_entities.items():
        entity_id = uuid.uuid4().hex[:16]
        conn.execute("INSERT INTO canonical_entities (entity_id, profile_id, canonical_name, "
                     "entity_type, first_seen, last_seen, fact_count) VALUES (?,?,?,?,?,?,0)",
                     (entity_id, p.profile_id, name, _guess_entity_type(name), now, now))
        conn.execute("INSERT INTO entity_aliases (alias_id, profile_id, entity_id, alias, "
                     "confidence, source) VALUES (?,?,?,?,1.0,'canonical')",
                     (uuid.uuid4().hex[:16], p.profile_id, entity_id, name))
        ids[f"new:{key}"] = entity_id
    return ids



def _names_changed() -> None:
    """Alias sources changed in place: recall's kept name index must rebuild."""
    from superlocalmemory.encoding import entity_lookup_index

    entity_lookup_index.invalidate()

def _fix_aliases(conn: sqlite3.Connection, p: elr.ProfilePlan, undo: _Undo) -> None:
    undo.put(p.profile_id, "aliases", [asdict(a) for a in p.aliases])
    for a in p.aliases:
        if a.new_source is None:
            conn.execute("DELETE FROM entity_aliases WHERE alias_id = ?", (a.alias_id,))
        else:
            conn.execute("UPDATE entity_aliases SET source = ? WHERE alias_id = ?",
                         (a.new_source, a.alias_id))
    _names_changed()


def _relink(conn: sqlite3.Connection, p: elr.ProfilePlan, ids: dict[str, str],
            undo: _Undo) -> list[str]:
    """New links for each fact, the index after them; the touched entities."""
    has_index = _table(conn, "fact_entity_associations")
    old_pairs = _select(conn, "fact_entity_associations", "profile_id = ? AND fact_id IN "
                        "({marks})", [f.fact_id for f in p.facts], (p.profile_id,)) \
        if has_index else []
    undo.put(p.profile_id, "facts", [[f.fact_id, f.old_json, json.dumps(
        [ids.get(i, i) for i in f.new_ids])] for f in p.facts])
    undo.put(p.profile_id, "associations", old_pairs)
    touched: set[str] = set()
    for f in p.facts:
        new = [ids.get(i, i) for i in f.new_ids]
        touched |= set(new) | set(elr._decode(f.old_json))
        conn.execute("UPDATE atomic_facts SET canonical_entities_json = ? WHERE fact_id = ?",
                     (json.dumps(new), f.fact_id))
        if has_index:
            conn.execute("DELETE FROM fact_entity_associations WHERE profile_id = ? AND "
                         "fact_id = ?", (p.profile_id, f.fact_id))
            conn.executemany(
                "INSERT OR IGNORE INTO fact_entity_associations (profile_id, fact_id, "
                "entity_id, first_operation_id, count_applied) SELECT ?,?,?,"
                "'entity-link-repair',0 FROM canonical_entities WHERE entity_id = ?",
                [(p.profile_id, f.fact_id, e, e) for e in new])
    return sorted(touched)


def _recount(conn: sqlite3.Connection, p: elr.ProfilePlan, ids: dict[str, str],
             touched: list[str], undo: _Undo) -> None:
    counts: dict[str, int] = dict.fromkeys(touched, 0)
    for links in p.links.values():
        for i in {ids.get(x, x) for x in links}:
            if i in counts:
                counts[i] += 1
    old = _select(conn, "canonical_entities", "entity_id IN ({marks})", touched)
    undo.put(p.profile_id, "fact_counts", [[r["entity_id"], r["fact_count"]] for r in old])
    conn.executemany("UPDATE canonical_entities SET fact_count = ? WHERE entity_id = ?",
                     [(n, e) for e, n in counts.items()])


def _apply_profile(conn: sqlite3.Connection, p: elr.ProfilePlan, undo: _Undo) -> dict:
    ids = _create_entities(conn, p)
    undo.put(p.profile_id, "created", sorted(ids.values()))
    edges = _edges_to_drop(conn, p, ids)
    events = _events_to_move(conn, p, ids)
    _fix_aliases(conn, p, undo)
    touched = _relink(conn, p, ids, undo)
    _recount(conn, p, ids, touched, undo)
    undo.put(p.profile_id, "edges", edges)
    for part in _chunks([e["edge_id"] for e in edges]):
        conn.execute(f"DELETE FROM graph_edges WHERE edge_id IN ({','.join('?' * len(part))})",
                     part)
    if any(t.startswith(elr.PLACEHOLDER_PREFIX) for _e, _o, t in events):
        placeholder = f"{elr.PLACEHOLDER_PREFIX}{p.profile_id}"
        if conn.execute("INSERT OR IGNORE INTO canonical_entities (entity_id, profile_id, "
                        "canonical_name, entity_type, fact_count) VALUES (?, ?, '', "
                        "'unresolved', 0)", (placeholder, p.profile_id)).rowcount:
            undo.put(p.profile_id, "created", [placeholder])
    undo.put(p.profile_id, "events", [[e, o, t] for e, o, t in events])
    conn.executemany("UPDATE temporal_events SET entity_id = ? WHERE event_id = ?",
                     [(t, e) for e, _o, t in events])
    junk = remove_junk(conn, p, undo)
    _enqueue_projection(conn, p.profile_id, [f.fact_id for f in p.facts] + [
        x for e in edges for x in (e["source_id"], e["target_id"])])
    return {"edges_removed": len(edges), "temporal_events_repointed": len(events),
            "entities_created": len(ids), "junk_entities_removed": junk}


def _enqueue_projection(conn: sqlite3.Connection, profile_id: str, fact_ids: list[str]) -> None:
    """Re-project these facts' graph links (storage/projection_outbox)."""
    if not fact_ids or not _table(conn, "projection_outbox"):
        return
    conn.executemany(
        "INSERT INTO projection_outbox (fact_id, profile_id, op, revision, enqueued_at, "
        "attempts, last_error) SELECT ?, ?, 'upsert', 1, ?, 0, NULL WHERE EXISTS "
        "(SELECT 1 FROM atomic_facts WHERE fact_id = ?) ON CONFLICT(fact_id) DO UPDATE SET "
        "op = 'upsert', revision = projection_outbox.revision + 1, "
        "enqueued_at = excluded.enqueued_at, attempts = 0, last_error = NULL",
        [(f, profile_id, _now(), f) for f in dict.fromkeys(fact_ids)])


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except BaseException:
        conn.close()
        raise
    return conn


def apply(db_path: str | Path, skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Plan and apply in one transaction; returns counts and the run id.

    ``skip``: ids an undone run touched, left alone (see ``entity_link_repair.plan``)."""
    from superlocalmemory.storage.write_lock import get_write_lock

    started = time.monotonic()
    conn = _connect(Path(db_path))
    try:
        with get_write_lock(Path(db_path)):
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = _apply_in(conn, skip)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
    unproject(result.pop("_removed", []))
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


def _apply_in(conn: sqlite3.Connection, skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    the_plan = elr.plan(conn, skip)
    summary: dict[str, Any] = {**the_plan.summary(), "edges_removed": 0,
                               "temporal_events_repointed": 0, "entities_created": 0,
                               "junk_entities_removed": 0}
    if the_plan.empty:
        return {**summary, "run_id": "", "changed": False}
    for statement in _DDL.split(";"):  # not executescript: it would commit
        if statement.strip():
            conn.execute(statement)
    run_id = uuid.uuid4().hex[:12]
    undo = _Undo(conn, run_id)
    for p in the_plan.profiles:
        for key, n in _apply_profile(conn, p, undo).items():
            summary[key] += n
    conn.execute(f"INSERT INTO {RUNS} VALUES (?,?,?,NULL)",
                 (run_id, _now(), json.dumps(summary, sort_keys=True)))
    removed = [e for p in the_plan.profiles for e in p.junk]
    return {**summary, "run_id": run_id, "changed": True, "_removed": removed}


BACKUP_LABEL = "pre-entity-repair"


def apply_with_backup(db_path: str | Path, root: str | Path) -> dict[str, Any]:
    """A backup copy of the store first (infra/backup), then ``apply``."""
    from superlocalmemory.infra.backup import BackupManager

    copy = BackupManager(db_path=Path(db_path), base_dir=Path(root)).create_backup(
        label=BACKUP_LABEL)
    if not copy:
        raise RuntimeError("the backup copy could not be made, so nothing was changed")
    return {**apply(db_path), "backup": copy}


def touched_ids(execute, run_ids: list[str]) -> frozenset[str]:
    """Every fact, alias and junk-entity id the given runs changed (from their undo record).

    ``execute(sql, params)`` returns rows; ids of undone runs are never re-planned."""
    if not run_ids or not execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (UNDO,)):
        return frozenset()
    out: set[str] = set()
    for run_id in run_ids:
        for row in execute(f"SELECT kind, payload FROM {UNDO} WHERE run_id = ? AND kind IN "
                           "('facts','aliases','junk_entities')", (run_id,)):
            for item in json.loads(row[1]):
                out.add(str(item[0] if row[0] == "facts" else
                            item["alias_id" if row[0] == "aliases" else "entity_id"]))
    return frozenset(out)


class UndoRefused(RuntimeError):
    """The run is unknown or already undone; nothing was changed."""


def undo(db_path: str | Path, run_id: str) -> dict[str, int]:
    """Put back what run ``run_id`` changed, in one transaction."""
    from superlocalmemory.storage.write_lock import get_write_lock

    conn = _connect(Path(db_path))
    try:
        with get_write_lock(Path(db_path)):
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = _undo_in(conn, run_id)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
    return result


def _load_undo(conn: sqlite3.Connection, run_id: str) -> dict[str, list]:
    if not _table(conn, RUNS):
        raise UndoRefused(f"no entity repair run {run_id!r} in this store")
    row = conn.execute(f"SELECT undone_at FROM {RUNS} WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise UndoRefused(f"no entity repair run {run_id!r} in this store")
    if row[0]:
        raise UndoRefused(f"run {run_id} was already undone at {row[0]}")
    parts: dict[str, list] = {}
    for profile_id, kind, payload in conn.execute(
            f"SELECT profile_id, kind, payload FROM {UNDO} WHERE run_id = ? ORDER BY seq",
            (run_id,)):
        parts.setdefault(kind, []).extend([profile_id, item] for item in json.loads(payload))
    return parts


def _undo_facts(conn: sqlite3.Connection, parts: dict[str, list], out: dict) -> list[str]:
    restored: list[str] = []
    profile_of: dict[str, str] = {}
    for profile_id, (fact_id, old_json, new_json) in parts.get("facts", []):
        cur = conn.execute("UPDATE atomic_facts SET canonical_entities_json = ? WHERE "
                           "fact_id = ? AND canonical_entities_json = ?",
                           (old_json, fact_id, new_json))
        if cur.rowcount:
            restored.append(fact_id)
            profile_of[fact_id] = profile_id
        else:
            out["facts_changed_since_left_alone"] += 1
    keep = set(restored)
    if keep and _table(conn, "fact_entity_associations"):
        conn.executemany("DELETE FROM fact_entity_associations WHERE profile_id = ? AND "
                         "fact_id = ?", [(profile_of[f], f) for f in keep])
        for _p, row in parts.get("associations", []):
            if row["fact_id"] in keep:
                cols = list(row)
                conn.execute(f"INSERT OR IGNORE INTO fact_entity_associations ({','.join(cols)})"
                             f" VALUES ({','.join('?' * len(cols))})", tuple(row.values()))
    out["facts_restored"] = len(restored)
    return restored


def _changes(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT total_changes()").fetchone()[0])


def _undo_rows(conn: sqlite3.Connection, parts: dict[str, list], out: dict) -> None:
    """Events, edges and aliases back, each kind in one batched statement."""
    start = _changes(conn)
    conn.executemany("UPDATE temporal_events SET entity_id = ? WHERE event_id = ? AND "
                     "entity_id = ?", [(o, e, n) for _p, (e, o, n) in parts.get("events", [])])
    out["temporal_events_restored"] += _changes(conn) - start
    edges = [row for _p, row in parts.get("edges", [])]
    if edges:
        cols = list(edges[0])
        start = _changes(conn)
        conn.executemany(f"INSERT OR IGNORE INTO graph_edges ({','.join(cols)}) VALUES "
                         f"({','.join('?' * len(cols))})", [tuple(r[c] for c in cols)
                                                            for r in edges])
        # The edge trigger writes one graph_generation row per insert.
        out["edges_restored"] += (_changes(conn) - start) // 2
    aliases = [a for _p, a in parts.get("aliases", [])]
    start = _changes(conn)
    conn.executemany("INSERT OR IGNORE INTO entity_aliases (alias_id, profile_id, entity_id, "
                     "alias, confidence, source) VALUES (?,?,?,?,?,?)",
                     [(a["alias_id"], a["profile_id"], a["entity_id"], a["alias"],
                       a["confidence"], a["source"]) for a in aliases if a["new_source"] is None])
    conn.executemany("UPDATE entity_aliases SET source = ? WHERE alias_id = ? AND source = ?",
                     [(a["source"], a["alias_id"], a["new_source"]) for a in aliases
                      if a["new_source"] is not None])
    out["aliases_restored"] += _changes(conn) - start
    _names_changed()
    conn.executemany("UPDATE canonical_entities SET fact_count = ? WHERE entity_id = ?",
                     [(n, e) for _p, (e, n) in parts.get("fact_counts", [])])


def _linked_ids(conn: sqlite3.Connection) -> set[str]:
    """Every entity id any fact links to (one pass over the facts)."""
    out: set[str] = set()
    for (raw,) in conn.execute("SELECT canonical_entities_json FROM atomic_facts "
                               "WHERE canonical_entities_json LIKE '[_%'"):
        out.update(elr._decode(raw))
    return out


def _undo_created(conn: sqlite3.Connection, parts: dict[str, list], out: dict) -> None:
    """Remove the entities the run created, unless something links them now."""
    created = [e for _p, e in parts.get("created", [])]
    if not created:
        return
    in_use = _linked_ids(conn)
    gone = [e for e in created if e not in in_use]
    out["created_entities_kept_in_use"] += len(created) - len(gone)
    for part in _chunks(gone):
        marks = ",".join("?" * len(part))
        for table in ("fact_entity_associations", "temporal_events", "entity_aliases"):
            if _table(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE entity_id IN ({marks})", part)
        out["created_entities_removed"] += conn.execute(
            f"DELETE FROM canonical_entities WHERE entity_id IN ({marks})", part).rowcount


def _undo_in(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    parts = _load_undo(conn, run_id)
    out = dict.fromkeys(("facts_restored", "facts_changed_since_left_alone",
                         "temporal_events_restored", "edges_restored", "aliases_restored",
                         "created_entities_removed", "created_entities_kept_in_use"), 0)
    out["junk_entities_restored"] = restore_junk(conn, parts)  # before the counts
    restore_lists(conn, parts)
    restored = _undo_facts(conn, parts, out)
    _undo_rows(conn, parts, out)
    _undo_created(conn, parts, out)
    by_profile: dict[str, list[str]] = {}
    for p, (fact_id, *_rest) in parts.get("facts", []):
        if fact_id in restored:
            by_profile.setdefault(p, []).append(fact_id)
    for p, row in parts.get("edges", []):
        by_profile.setdefault(p, []).extend((row["source_id"], row["target_id"]))
    for p, facts in by_profile.items():
        _enqueue_projection(conn, p, facts)
    conn.execute(f"UPDATE {RUNS} SET undone_at = ? WHERE run_id = ?", (_now(), run_id))
    return out


__all__ = ["BACKUP_LABEL", "RUNS", "UNDO", "UndoRefused", "apply", "apply_with_backup",
           "count_effects", "touched_ids", "undo"]
