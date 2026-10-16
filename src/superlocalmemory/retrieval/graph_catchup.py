# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""The entity graph follows writes by their size; it is not rebuilt per write.

WHY
------------------------------------
After every write the next recall reloaded the entity maps, the graph metrics
and the adjacency cache of a 25.6k-node / 611k-edge store: 2.3 s quiet, 5+ s
under load ("Loaded adjacency cache" 127 times in one log). 4.1.22 had moved
that rebuild beside recall (retrieval/adjacency_refresh), but under Mode B
enrichment a write landed every few seconds, so a full rebuild was ALWAYS
running: 2-5 s of pure-Python CPU per write, holding the GIL in long stretches
while the recall thread queued behind it on every SQLite call it made, and
re-allocating hundreds of MB per rebuild (RSS 5.9 -> 7.5 GB in 3 h). Recall
went from 0.5-0.6 s to 2.7-5.7 s and the answer check lost its budget.

WHAT THIS DOES (decision)
-------------------------
Every slot remembers the point in the store it reflects: the head of the
trigger-maintained change log (storage/fact_search_changes) and the highest
``graph_edges`` rowid it has read. A recall that finds the store moved reads
ONLY what moved -- the changed facts' current rows, the links past the rowid
watermark, the links of facts that just became visible -- and splices them
into a new immutable snapshot (retrieval/graph_delta). Milliseconds, on the
recall's clock, so a memory saved a second ago is already in the graph.

* Removals are never deferred. A fact deleted, erased, withheld, soft-deleted
  or moved out of scope is in the log; it leaves the snapshot before the walk
  runs, so a stale graph can neither return it nor carry activation through
  it. If the log cannot prove that (no log, a reset log, or a reader further
  behind than its retention) the slot is not used at all: rebuilt, exactly as
  before.
* Additions MAY be deferred, but only when a backlog is too large for a
  recall's clock (``SLM_GRAPH_DELTA_MAX_FACTS`` changed facts, default 2,000,
  or ``SLM_GRAPH_DELTA_MAX_EDGES`` new links, default 50,000: a bulk import).
  Then the recall applies the removals, ONE background rebuild is started, and
  ``graph_lag_s`` tells the caller how many seconds its graph is behind. A new
  fact is still found by the semantic and lexical channels meanwhile; the
  graph only boosts.
* Full rebuilds still happen, in the background only ("compaction"): to fold
  a deferred backlog, to drop duplicate rows once spliced rows exceed a quarter
  of the graph, and -- at most once per ``SLM_ENTITY_ADJ_TTL_S`` (3,600 s) --
  when the store changed in ways the log does not describe link by link
  (pruned or re-weighted links), or as soon as maintenance recomputes PageRank
  and communities (checked every ``SLM_GRAPH_METRICS_CHECK_S``, 60 s).

STALENESS BOUND: links and entity links are current at every recall unless a
backlog was deferred (then ``graph_lag_s`` > 0 until the background rebuild
lands, typically one build time, 2-3 s on the owner's store); PageRank and
communities trail maintenance by at most 60 s plus one build; pruned or
re-weighted links between live facts trail by at most the TTL. Removed facts:
zero.

REJECTED
--------
* Rebuild beside recall on every write (4.1.22): correct but the CPU and
  allocation cost per write was the defect, not where it ran.
* Throttled periodic rebuilds: still a 2-3 s GIL-bound job every period, and
  new memories would miss the graph for the whole period.
* An overlay the walk consults (base CSR + side COO + tombstone mask): no
  rebuild either, but it puts a second edge source and a liveness mask in the
  hot loop of retrieval/spreading, which the parity tests pin bit for bit.
  Splicing keeps the walk untouched and the snapshot a plain CSR.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from dataclasses import replace
from typing import Any, Iterable

from superlocalmemory.retrieval.graph_delta import GraphDelta, splice
from superlocalmemory.storage import fact_search_changes as changes
from superlocalmemory.storage.database import _scope_where

logger = logging.getLogger(__name__)

#: Change-log kinds that can move a fact in the graph (plus '*').
_GRAPH_WHATS = ("v", "e")
_CHUNK = 500


def _env_number(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return float(default)


def max_delta_facts() -> int:
    return int(_env_number("SLM_GRAPH_DELTA_MAX_FACTS", 2000))


def max_delta_edges() -> int:
    return int(_env_number("SLM_GRAPH_DELTA_MAX_EDGES", 50000))


# -- where a slot stands in the store ----------------------------------------

def _max_edge_rowid(db: Any) -> int:
    rows = db.execute("SELECT MAX(rowid) AS top FROM graph_edges")
    return int((dict(rows[0]).get("top") if rows else 0) or 0)


def _graph_version(db: Any, scope_key: tuple) -> str:
    from superlocalmemory.storage.graph_generation import graph_version

    try:
        return graph_version(db, scope_key[0], cross_scope=bool(scope_key[1] or scope_key[2]))
    except Exception:  # noqa: BLE001 -- unknown: compare as "changed"
        return ""


def _metrics_signature(db: Any, profile_id: str) -> str | None:
    """When PageRank/communities were last computed for the profile.

    Only a NEWER computation is a reason to rebuild: deleting a fact removes
    its row too (a count would then move on every delete, and every delete
    would cost a full rebuild again).
    """
    try:
        rows = db.execute("SELECT MAX(computed_at) AS at FROM fact_importance "
                          "WHERE profile_id = ?", (profile_id,))
        at = dict(rows[0]).get("at") if rows else None
        return str(at) if at else None
    except Exception:  # noqa: BLE001 -- no metrics table: nothing to follow
        return None


def _store_identity(db: Any) -> tuple[int, int] | None:
    """The memory.db file's (device, inode): a restored or swapped file is a new store."""
    path = getattr(db, "db_path", None)
    if not path:
        return None
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def read_watermark(db: Any) -> tuple[int, int] | None:
    """``(log head, edge rowid)`` to read BEFORE a full load, or None.

    Taken before the load, every change committed during it is past the
    watermark and is applied again afterwards; applying a change twice is
    harmless (graph_delta), missing one is not.
    """
    try:
        head = _log_bounds(db)[0]
        return head, _max_edge_rowid(db)
    except Exception:  # noqa: BLE001 -- no log: the slot cannot follow writes
        return None


def _log_bounds(db: Any) -> tuple[int, int | None]:
    """Two primary-key seeks in ONE read snapshot, rather than an aggregate scan.

    SQLite optimizes a lone MIN or MAX, but scans every retained row when
    both appear in one aggregate. Scalar subqueries preserve the atomic view
    of the bounds and let each extrema seek stop after one row.
    """
    rows = db.execute(f"SELECT (SELECT MAX(seq) FROM {changes.TABLE}) AS head, "
                      f"(SELECT MIN(seq) FROM {changes.TABLE}) AS oldest")
    row = dict(rows[0]) if rows else {}
    oldest = row.get("oldest")
    return int(row.get("head") or 0), (int(oldest) if oldest is not None else None)


def stamp(channel: Any, slot: Any, scope_key: tuple, watermark: tuple[int, int] | None,
          *, now: float) -> Any:
    """A freshly loaded slot, slimmed to its arrays and marked with its watermark."""
    snap = slot.snapshot
    slim = replace(slot, adj={}, fact_to_entities={}, graph_metrics={},
                   entity_to_facts=snap.entity_to_facts,
                   visible_fact_ids=snap.node_index.keys())
    if watermark is None:
        return slim
    seq, rowid = watermark
    return replace(slim, seq=seq, removed_seq=seq, edge_rowid=rowid, built_at=now,
                   store_id=_store_identity(channel._db),
                   built_sig=(seq, rowid, _graph_version(channel._db, scope_key)),
                   metrics_sig=_metrics_signature(channel._db, scope_key[0]),
                   metrics_checked_at=now)


# -- reading what moved --------------------------------------------------------

def _changed_facts(db: Any, after: int, upto: int) -> list[str]:
    marks = ",".join("?" for _ in _GRAPH_WHATS)
    rows = db.execute(
        f"SELECT DISTINCT fact_id FROM {changes.TABLE} WHERE seq > ? AND seq <= ? "
        f"AND what IN ('*', {marks})", (after, upto, *_GRAPH_WHATS))
    return [str(dict(r)["fact_id"]) for r in rows]


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start:start + _CHUNK]


def _entities(raw: Any) -> tuple[str, ...]:
    try:
        ids = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(e for e in ids if isinstance(e, str) and e) if isinstance(ids, list) else ()


def _visible_rows(db: Any, scope_key: tuple, fact_ids: list[str]
                  ) -> dict[str, tuple[tuple[str, ...], str]]:
    """The facts among ``fact_ids`` a fresh build would load: entities, owner."""
    profile_id, include_global, include_shared = scope_key
    where, params = _scope_where(profile_id, include_global=include_global,
                                 include_shared=include_shared)
    visible = db.visible_fact_clause()
    out: dict[str, tuple[tuple[str, ...], str]] = {}
    for part in _chunks(fact_ids):
        marks = ",".join("?" for _ in part)
        rows = db.execute(
            f"SELECT fact_id, profile_id, canonical_entities_json FROM atomic_facts "
            f"WHERE {where}{visible} AND fact_id IN ({marks})", (*params, *part))
        for row in map(dict, rows):
            out[str(row["fact_id"])] = (_entities(row.get("canonical_entities_json")),
                                        str(row["profile_id"]))
    return out


_EDGE_COLS = "SELECT rowid AS rid, source_id, target_id, weight FROM graph_edges WHERE "


def _links_of(db: Any, where: str, params: list, owners: dict[str, list[str]]
              ) -> list[Any]:
    """Every link naming one of the facts, by the (profile, endpoint) indexes.

    One query per owner profile and endpoint column, so SQLite seeks
    ``idx_edges_source`` / ``idx_edges_target`` instead of walking the whole
    profile (measured: 2-5 s per catch-up on 611k edges with an OR).
    """
    # ``+profile_id`` keeps the scope test from being the index SQLite picks
    # (it otherwise prefers idx_edges_profile and walks the whole profile).
    where = where.replace("(profile_id", "(+profile_id")
    batches = []
    for owner, facts in owners.items():
        for part in _chunks(facts):
            marks = ",".join("?" for _ in part)
            for column in ("source_id", "target_id"):
                batches.append(db.execute(
                    f"{_EDGE_COLS}profile_id = ? AND {column} IN ({marks}) AND {where}",
                    (owner, *part, *params)))
    return batches


def _edge_rows(db: Any, scope_key: tuple, after_rowid: int,
               owners: dict[str, list[str]]) -> list[tuple[str, str, float]]:
    """Links past the rowid watermark, plus every link of the facts in ``owners``."""
    profile_id, include_global, include_shared = scope_key
    where, params = _scope_where(profile_id, include_global=include_global,
                                 include_shared=include_shared)
    batches = [db.execute(f"{_EDGE_COLS}rowid > ? AND {where}", (after_rowid, *params))]
    batches += _links_of(db, where, params, owners)
    found: dict[int, tuple[str, str, float]] = {}
    for rows in batches:
        for row in map(dict, rows):
            found[int(row["rid"])] = (str(row["source_id"]), str(row["target_id"]),
                                      float(row["weight"]))
    return list(found.values())


def _metrics_of(db: Any, profile_id: str, fact_ids: list[str]) -> dict[str, tuple[float, int]]:
    out: dict[str, tuple[float, int]] = {}
    for part in _chunks(fact_ids):
        marks = ",".join("?" for _ in part)
        try:
            rows = db.execute(
                "SELECT fact_id, pagerank_score, community_id FROM fact_importance "
                f"WHERE profile_id = ? AND fact_id IN ({marks})", (profile_id, *part))
        except Exception:  # noqa: BLE001 -- unmeasured: 0 / -1, as a fresh build
            return out
        for row in map(dict, rows):
            comm = row.get("community_id")
            try:
                comm = int(comm) if comm is not None else -1
            except (TypeError, ValueError):
                comm = -1
            out[str(row["fact_id"])] = (float(row.get("pagerank_score") or 0.0), comm)
    return out


# -- applying it ----------------------------------------------------------------

def _apply_all(channel: Any, scope_key: tuple, slot: Any, head: int, top: int) -> Any:
    db, snap = channel._db, slot.snapshot
    changed = _changed_facts(db, slot.seq, head)
    rows = _visible_rows(db, scope_key, changed)
    removed = frozenset(f for f in changed if f not in rows)
    owners: dict[str, list[str]] = {}
    for fid, (_, owner) in rows.items():
        if fid not in snap.node_index:
            owners.setdefault(owner, []).append(fid)
    appeared = [f for facts in owners.values() for f in facts]
    edges = _edge_rows(db, scope_key, min(slot.edge_rowid, top), owners)
    delta = GraphDelta(removed=removed, facts={f: ents for f, (ents, _) in rows.items()},
                       edges=edges, metrics=_metrics_of(db, scope_key[0], appeared))
    return _with_snapshot(slot, splice(snap, delta), seq=head, removed_seq=head,
                          edge_rowid=top, behind_since=None,
                          spliced=slot.spliced + len(edges) + len(appeared))


def _apply_removals(channel: Any, scope_key: tuple, slot: Any, head: int,
                    *, now: float) -> Any:
    db, snap = channel._db, slot.snapshot
    present = [f for f in _changed_facts(db, slot.removed_seq, head) if f in snap.node_index]
    still = _visible_rows(db, scope_key, present)
    gone = frozenset(f for f in present if f not in still)
    return _with_snapshot(slot, splice(snap, GraphDelta(removed=gone)), removed_seq=head,
                          behind_since=slot.behind_since if slot.behind_since else now)


def _with_snapshot(slot: Any, snap: Any, **fields: Any) -> Any:
    return replace(slot, snapshot=snap, entity_to_facts=snap.entity_to_facts,
                   visible_fact_ids=snap.node_index.keys(), edge_count=snap.edge_count,
                   fact_count=snap.node_count, **fields)


def catch_up(channel: Any, scope_key: tuple, slot: Any, *, now: float) -> Any | None:
    """``slot`` brought to the store's present, or None when that cannot be proven.

    None means the caller must not serve this slot: rebuild it (or report the
    channel warming), as for a slot that never existed.
    """
    if slot.seq is None or slot.snapshot is None:
        return None
    db = channel._db
    if slot.store_id != _store_identity(db):
        return None  # the file was replaced (restore): its log is another log
    try:
        head, oldest = _log_bounds(db)
        top = _max_edge_rowid(db)
        removed_from = slot.removed_seq if slot.removed_seq is not None else slot.seq
        if head < removed_from:
            return None  # the log was reset under us (restore): prove nothing
        if head > removed_from and oldest is not None and oldest > removed_from + 1:
            return None  # further behind than the log keeps
        if head == slot.seq and top == slot.edge_rowid:
            return slot
        if head - slot.seq <= max_delta_facts() and top - slot.edge_rowid <= max_delta_edges():
            return _apply_all(channel, scope_key, slot, head, top)
        caught = _apply_removals(channel, scope_key, slot, head, now=now)
    except Exception as exc:  # noqa: BLE001 -- cannot prove it current: rebuild
        logger.warning("entity graph catch-up failed (%s); rebuilding", type(exc).__name__)
        return None
    request_compaction(channel, scope_key, reason="backlog")
    return caught


# -- background compaction --------------------------------------------------------

_inflight: dict[tuple[int, tuple], threading.Thread] = {}
_inflight_lock = threading.Lock()


def _compact(channel: Any, scope_key: tuple, key: tuple[int, tuple], epoch: int) -> None:
    try:
        db = channel._db
        watermark = read_watermark(db)
        shadow = copy.copy(channel)  # the loader writes only its own fields
        profile_id, include_global, include_shared = scope_key
        now = time.monotonic()
        fresh = shadow._load_adjacency_from_db(
            profile_id, include_global=include_global, include_shared=include_shared,
            current_count=0, current_fact_count=-1, now=now)
        fresh = stamp(channel, fresh, scope_key, watermark, now=now)
        fresh = catch_up(channel, scope_key, fresh, now=time.monotonic()) or fresh
        with channel._cache_lock:
            if getattr(channel, "_graph_epoch", 0) == epoch and scope_key in channel._adj_slots:
                channel._adj_slots[scope_key] = fresh
    except Exception as exc:  # noqa: BLE001 -- the next trigger tries again
        logger.warning("background entity graph rebuild failed (%s)", type(exc).__name__)
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)


def request_compaction(channel: Any, scope_key: tuple, *, reason: str = "") -> bool:
    """Start one background full rebuild of ``scope_key`` unless one is running.

    At most one per ``SLM_GRAPH_COMPACT_MIN_GAP_S`` (30 s) per scope, so a
    rebuild that keeps failing, or a store written faster than it can be
    rebuilt, never turns back into a rebuild per recall.
    """
    key = (id(channel), scope_key)
    now = time.monotonic()
    with _inflight_lock:
        last = channel.__dict__.setdefault("_graph_compact_at", {})
        gap = _env_number("SLM_GRAPH_COMPACT_MIN_GAP_S", 30)
        if key in _inflight or (scope_key in last and now - last[scope_key] < gap):
            return False
        last[scope_key] = now
        thread = threading.Thread(
            target=_compact, args=(channel, scope_key, key, getattr(channel, "_graph_epoch", 0)),
            name="slm-graph-compact", daemon=True)
        _inflight[key] = thread
    logger.info("entity graph for %s rebuilding in the background (%s)", scope_key[0], reason)
    thread.start()
    return True


def wait_idle(channel: Any, timeout: float = 30.0) -> bool:
    """Join this channel's background rebuilds (tests, shutdown)."""
    end = time.monotonic() + timeout
    while True:
        with _inflight_lock:
            threads = [t for (cid, _), t in _inflight.items() if cid == id(channel)]
        if not threads:
            return True
        for thread in threads:
            thread.join(max(0.0, end - time.monotonic()))
        if time.monotonic() >= end:
            with _inflight_lock:
                return not any(cid == id(channel) for cid, _ in _inflight)


def maybe_compact(channel: Any, scope_key: tuple, slot: Any, *, now: float) -> Any:
    """Start a background rebuild when the slot has drifted; returns the slot to keep."""
    snap = slot.snapshot
    if slot.spliced > max(5000, snap.edge_count // 4 if snap is not None else 0):
        request_compaction(channel, scope_key, reason="spliced rows")
        return slot
    ttl = _env_number("SLM_ENTITY_ADJ_TTL_S", 3600)
    if ttl > 0 and now - slot.built_at >= ttl:
        sig = (slot.seq, slot.edge_rowid, _graph_version(channel._db, scope_key))
        if sig != slot.built_sig:
            request_compaction(channel, scope_key, reason="age")
        return replace(slot, built_at=now)
    if now - slot.metrics_checked_at >= _env_number("SLM_GRAPH_METRICS_CHECK_S", 60):
        sig = _metrics_signature(channel._db, scope_key[0])
        if sig is not None and (slot.metrics_sig is None or sig > slot.metrics_sig):
            request_compaction(channel, scope_key, reason="graph metrics")
        return replace(slot, metrics_checked_at=now)
    return slot


def follow(channel: Any, scope_key: tuple, slot: Any, *, now: float) -> Any | None:
    """The recall-path entry: catch up, then decide on a background rebuild."""
    caught = catch_up(channel, scope_key, slot, now=now)
    if caught is None:
        return None
    return maybe_compact(channel, scope_key, caught, now=now)


def lag_seconds(slot: Any, *, now: float | None = None) -> float:
    """How far the slot's graph is behind the store, in seconds (0.0 = current)."""
    since = getattr(slot, "behind_since", None)
    if since is None:
        return 0.0
    return round(max(0.0, (time.monotonic() if now is None else now) - since), 3)


__all__ = ["catch_up", "follow", "lag_seconds", "max_delta_edges", "max_delta_facts",
           "maybe_compact", "read_watermark", "request_compaction", "stamp", "wait_idle"]
