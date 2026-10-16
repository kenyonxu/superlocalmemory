# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SuperLocalMemory V3 — Entity Graph Channel with Spreading Activation.

SA-RAG pattern: entities from query -> canonical lookup -> graph traversal
with decay. Handles BOTH uppercase and lowercase entity mentions.

Part of Qualixar | Author: Varun Pratap Bhardwaj
License: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Any

from superlocalmemory.encoding.entity_match import alias_in_use as _alias_in_use
from superlocalmemory.retrieval import adjacency_load as _load
from superlocalmemory.retrieval import adjacency_rcu as _rcu
from superlocalmemory.retrieval import graph_catchup as _catchup
from superlocalmemory.retrieval import spreading
from superlocalmemory.retrieval.adjacency_slot import AdjSlot as _AdjSlot
from superlocalmemory.retrieval.adjacency_slot import adj_cache_profiles as _adj_cache_profiles
from superlocalmemory.retrieval.adjacency_slot import adj_ttl_seconds as _adj_ttl_seconds

# Question-side names (retrieval/query_entities, the rules a save uses);
# extract_query_entities stays importable from here for its callers.
from superlocalmemory.retrieval.query_entities import (
    extract_query_entities,
    query_plain_words,
)
from superlocalmemory.retrieval.scope_policy import (
    authorized_fact_ids,
    filter_authorized_results,
)

if TYPE_CHECKING:
    from superlocalmemory.encoding.entity_resolver import EntityResolver
    from superlocalmemory.storage.database import DatabaseManager

logger = logging.getLogger(__name__)


def ranked_authorized_topk(db: Any, ranked: list[tuple[str, float]], profile_id: str,
                           top_k: int, **scope: bool) -> list[tuple[str, float]]:
    """Authorize only enough ranks to fill a page, without dropping candidates.

    Authorization preserves order and scores. Walking the sorted prefix and
    backfilling rejected ranks is therefore exactly full authorization followed
    by slicing. No visibility cache is used: revocations take effect on each read.
    After the first rejection, scan at least 64 ranks per authorization query
    so sparse visibility cannot turn the remaining budget into one-row queries.
    Negative slices retain the historical Python slicing behaviour.
    """
    if top_k < 0:
        return filter_authorized_results(db, ranked, profile_id, **scope)[:top_k]
    out: list[tuple[str, float]] = []
    cursor = 0
    while len(out) < top_k and cursor < len(ranked):
        remaining = top_k - len(out)
        size = min(800, max(64, remaining) if cursor else remaining)
        page = ranked[cursor:cursor + size]
        out.extend(filter_authorized_results(db, page, profile_id, **scope))
        cursor += size
    return out[:top_k]


class EntityGraphChannel:
    """Entity-based retrieval with spreading activation (SA-RAG).

    V3.3.9: In-memory adjacency cache for O(1) edge lookup.
    Replaces per-node SQLite queries (23ms each) with dict lookup (<0.001ms).
    The cache is loaded once per scope; after that a write is spliced into it
    by the write's own size (retrieval/graph_catchup) instead of reloading it.
    Memory: ~20.2 MB of arrays at 611k undirected edges / 25.6k nodes,
    plus workload-dependent Python maps (retrieval/adjacency_slot).

    Per-request profiles: the cache holds the last SLM_ADJ_CACHE_PROFILES
    (default 3) profile scopes in an LRU, so alternating requests do not
    thrash a single slot into a full graph rebuild each time.
    """

    def __init__(
        self,
        db: DatabaseManager,
        entity_resolver: EntityResolver | None = None,
        decay: float = 0.7,
        activation_threshold: float = 0.05,
        max_hops: int = 4,
        graph_metrics: dict[str, dict] | None = None,
        cozo_backend: Any = None,  # v3.4.5: optional CozoDB backend
    ) -> None:
        self._db = db
        self._resolver = entity_resolver
        self._decay = decay
        self._threshold = activation_threshold
        self._max_hops = max_hops
        # v3.4.5: Optional CozoDB graph backend (Sprint 2)
        self._cozo = cozo_backend
        self._cache_lock = threading.RLock()
        # Profile-keyed LRU of adjacency loads, oldest first. Keyed by the full
        # scope (profile, include_global, include_shared) because each scope is
        # a different graph. The legacy single-slot attributes below always
        # mirror the scope served by the most recent _ensure_adjacency call.
        self._adj_slots: OrderedDict[tuple[str, bool, bool], _AdjSlot] = OrderedDict()
        # In-memory adjacency: {node_id -> [(neighbor_id, weight), ...]}
        self._adj: dict[str, list[tuple[str, float]]] = {}
        self._adj_profile: str = ""  # Track which profile is loaded
        self._adj_scope_key: tuple[str, bool, bool] | None = None
        self._adj_edge_count: int = 0  # Track edge count for staleness detection
        self._adj_fact_count: int = 0
        self._adj_loaded_at: float = 0.0  # TTL reference for the current slot
        self._entity_to_facts: dict[str, list[str]] = defaultdict(list)
        self._fact_to_entities: dict[str, list[str]] = defaultdict(list)
        self._visible_fact_ids: set[str] = set()
        # v3.4.1: Graph intelligence metrics (loaded from fact_importance)
        self._graph_metrics: dict[str, dict] = graph_metrics or {}
        self._graph_metrics_profile: str = ""

    def _store_counts(self, scope_key: tuple, *, include_global: bool,
                      include_shared: bool) -> tuple[int, int]:
        """The scope's edge and fact counts, which decide whether the cached
        graph is current. Counting 481k edges took 44 ms of every recall; while
        nothing has been committed since (storage/store_signature) the last
        counts are still exact and are reused."""
        from superlocalmemory.storage import store_signature

        sig = store_signature.of_db(self._db)
        memo = self.__dict__.setdefault("_count_memo", {})
        hit = memo.get(scope_key)
        if sig is not None and hit is not None and hit[0] == sig:
            return hit[1], hit[2]
        profile_id = scope_key[0]
        edges = self._get_edge_count(profile_id, include_global=include_global,
                                     include_shared=include_shared)
        try:
            facts = self._db.get_fact_count(profile_id, include_global=include_global,
                                            include_shared=include_shared)
        except Exception:
            facts = -1
        if sig is not None:
            memo[scope_key] = (sig, edges, facts)
        return edges, facts

    def _ensure_adjacency(
        self,
        profile_id: str,
        *,
        include_global: bool = False,
        include_shared: bool = False,
    ) -> None:
        """Make the scope's cached graph current (an LRU of
        ``SLM_ADJ_CACHE_PROFILES`` scopes). A cached graph follows writes by
        their size (retrieval/graph_catchup); only a first build, an evicted
        scope, or one whose currency cannot be proven is built, off the lock
        (retrieval/adjacency_rcu: may raise GraphWarming).
        """
        scope_key = (profile_id, bool(include_global), bool(include_shared))
        now = time.monotonic()
        slot = self._adj_slots.get(scope_key)
        if slot is not None:
            usable = self._usable_slot(scope_key, slot, now)
            if usable is not None:
                self._adj_slots[scope_key] = usable
                self._adj_slots.move_to_end(scope_key)
                return self._restore_slot(scope_key, usable)
        current_count, current_fact_count = self._store_counts(
            scope_key, include_global=include_global, include_shared=include_shared)
        slot = _rcu.build_slot(self, scope_key, current_count=current_count,
                               current_fact_count=current_fact_count, now=now)
        self._restore_slot(scope_key, slot)
        self._adj_slots.move_to_end(scope_key)
        while len(self._adj_slots) > _adj_cache_profiles():
            self._adj_slots.popitem(last=False)

    def _usable_slot(self, scope_key: tuple[str, bool, bool], slot: _AdjSlot,
                     now: float) -> _AdjSlot | None:
        """The cached slot brought current, or None when it must be rebuilt."""
        if slot.seq is not None:
            return _catchup.follow(self, scope_key, slot, now=now)
        # A store without the change log keeps the count-and-age check it had.
        if not (slot.adj or slot.visible_fact_ids):
            return None
        edges, facts = self._store_counts(scope_key, include_global=scope_key[1],
                                          include_shared=scope_key[2])
        ttl = _adj_ttl_seconds()
        fresh = ttl <= 0.0 or (now - slot.loaded_at) < ttl
        if slot.edge_count == edges and slot.fact_count == facts and fresh:
            return slot
        return None

    def graph_lag_s(self, profile_id: str, *, include_global: bool | None = None,
                    include_shared: bool | None = None) -> float | None:
        """Seconds the scope's cached graph is behind the store; None if none.

        0.0 whenever every change in the store is in the graph -- the normal
        case, since writes are spliced in on the next recall. Positive only
        while a backlog too large for a recall waits for its background
        rebuild (retrieval/graph_catchup).
        """
        if include_global is None:
            include_global = bool(getattr(self, "include_global", False))
        if include_shared is None:
            include_shared = bool(getattr(self, "include_shared", False))
        with self._cache_lock:
            slot = self._adj_slots.get((profile_id, bool(include_global), bool(include_shared)))
        return None if slot is None else _catchup.lag_seconds(slot)

    def _restore_slot(
        self,
        scope_key: tuple[str, bool, bool],
        slot: _AdjSlot,
    ) -> None:
        """Point the legacy single-slot attributes at a cached LRU slot.

        Everything downstream of _ensure_adjacency (search, score_candidates,
        _resolve_entities) reads these attributes; refreshing them per call is
        what keeps those readers untouched by the multi-slot cache.
        """
        self._adj = slot.adj
        self._adj_profile = scope_key[0]
        self._adj_scope_key = scope_key
        self._adj_edge_count = slot.edge_count
        self._adj_fact_count = slot.fact_count
        self._adj_loaded_at = slot.loaded_at
        self._entity_to_facts = slot.entity_to_facts
        self._fact_to_entities = slot.fact_to_entities
        self._visible_fact_ids = slot.visible_fact_ids
        self._graph_metrics = slot.graph_metrics
        self._graph_metrics_profile = slot.graph_metrics_profile
        self._adjacency_source_name = slot.adjacency_source_name
        self._snapshot = slot.snapshot

    def _load_adjacency_from_db(
        self,
        profile_id: str,
        *,
        include_global: bool = False,
        include_shared: bool = False,
        current_count: int = 0,
        current_fact_count: int = -1,
        now: float = 0.0,
    ) -> _AdjSlot:
        """Fetch one scope's graph from the stores and build its cache slot
        (retrieval/adjacency_load). A method so tests and the RCU builder
        can call or wrap it on a shallow copy of the channel."""
        return _load.load_adjacency_from_db(
            self, profile_id, include_global=include_global,
            include_shared=include_shared, current_count=current_count,
            current_fact_count=current_fact_count, now=now)

    def _projection_is_caught_up(self, profile_id: str) -> bool:
        return _load.projection_is_caught_up(self, profile_id)

    def _get_edge_count(self, profile_id: str, *, include_global: bool = False,
                        include_shared: bool = False) -> int:
        return _load.get_edge_count(self, profile_id, include_global=include_global,
                                    include_shared=include_shared)

    def _load_entity_maps(self, profile_id: str, *, include_global: bool = False,
                          include_shared: bool = False) -> None:
        _load.load_entity_maps(self, profile_id, include_global=include_global,
                               include_shared=include_shared)

    def _load_graph_metrics(self, profile_id: str) -> None:
        _load.load_graph_metrics(self, profile_id)

    def invalidate_cache(self) -> None:
        """Clear all caches. Call after adding/removing edges or facts.

        4.1.14 audit: takes the cache lock like every other cache
        accessor. Reassigns rather than clearing in place: a slot's maps are
        shared with snapshots a reader may still be walking. Bumping the epoch
        stops a background rebuild started before this from installing.
        """
        with self._cache_lock:
            self._adj_slots.clear()
            self._graph_epoch = getattr(self, "_graph_epoch", 0) + 1
            self._adj = {}
            self._adj_profile = ""
            self._adj_scope_key = None
            self._adj_edge_count = 0
            self._adj_fact_count = 0
            self._entity_to_facts = defaultdict(list)
            self._fact_to_entities = defaultdict(list)
            self._visible_fact_ids = set()
            self._graph_metrics = {}
            self._graph_metrics_profile = ""
            self._snapshot = None

    def search(self, query: str, profile_id: str, top_k: int = 50) -> list[tuple[str, float]]:
        """Serialize access to the scope-keyed graph/entity cache."""
        with self._cache_lock:
            return self._search_locked(query, profile_id, top_k)

    def _search_locked(self, query: str, profile_id: str,
                       top_k: int = 50) -> list[tuple[str, float]]:
        """Resolve a question, walk its scope, and authorize the ranked page."""
        include_global = bool(getattr(self, "include_global", False))
        include_shared = bool(getattr(self, "include_shared", False))
        raw, plain = extract_query_entities(query), query_plain_words(query)
        if not raw and not plain:
            return []
        self._ensure_adjacency(profile_id, include_global=include_global,
                               include_shared=include_shared)
        ids = self._resolve_entities(raw, profile_id, plain)
        if not ids:
            return []
        snapshot = getattr(self, "_snapshot", None)
        if snapshot is not None and snapshot.node_count:
            result = spreading.activate(snapshot, ids, decay=self._decay,
                                        threshold=self._threshold, max_hops=self._max_hops)
            spreading.apply_community_bias(result.scores, snapshot, ids)
            activation = result.as_mapping(snapshot, threshold=-1.0)
            if activation:
                self._suppress_contradictions(activation, profile_id)
        else:
            activation = self._legacy_activation(ids, profile_id)
        return self._rank_page(activation, profile_id, top_k, include_global, include_shared)

    def _rank_page(self, activation: dict[str, float], profile_id: str, top_k: int,
                   include_global: bool, include_shared: bool) -> list[tuple[str, float]]:
        results = [(fid, sc) for fid, sc in activation.items() if sc >= self._threshold]
        if not results:
            return []
        max_score = max(sc for _, sc in results)
        if max_score > 0:
            results = [(fid, sc / max_score) for fid, sc in results]
        results.sort(key=lambda x: (-x[1], x[0]))
        return ranked_authorized_topk(self._db, results, profile_id, top_k,
                                       include_global=include_global, include_shared=include_shared)

    def _legacy_activation(self, canonical_ids: list[str],
                           profile_id: str) -> dict[str, float]:
        """Lightweight/mock stores without a snapshot retain the original walk."""
        include_global = bool(getattr(self, "include_global", False))
        include_shared = bool(getattr(self, "include_shared", False))
        activation: dict[str, float] = defaultdict(float)
        visited_entities: set[str] = set(canonical_ids)
        for eid in canonical_ids:
            for fact in self._db.get_facts_by_entity(
                eid,
                profile_id,
                include_global=include_global,
                include_shared=include_shared,
            ):
                activation[fact.fact_id] = max(activation[fact.fact_id], 1.0)

        return self._legacy_hops(activation, visited_entities, profile_id)

    def _legacy_hops(self, activation: dict[str, float], visited_entities: set[str],
                     profile_id: str) -> dict[str, float]:
        frontier = set(activation)
        include_global = bool(getattr(self, "include_global", False))
        include_shared = bool(getattr(self, "include_shared", False))
        for hop in range(1, self._max_hops):
            hop_decay = self._decay**hop
            if hop_decay < self._threshold:
                break
            next_frontier: set[str] = set()
            for fid in frontier:
                for edge in self._db.get_edges_for_node(
                    fid,
                    profile_id,
                    include_global=include_global,
                    include_shared=include_shared,
                ):
                    neighbor = edge.target_id if edge.source_id == fid else edge.source_id
                    propagated = activation[fid] * self._decay
                    if propagated >= self._threshold and propagated > activation.get(
                        neighbor, 0.0
                    ):
                        activation[neighbor] = propagated
                        next_frontier.add(neighbor)
            new_eids_sql = self._discover_entities(frontier, profile_id, visited_entities)
            for eid in new_eids_sql:
                visited_entities.add(eid)
                for fact in self._db.get_facts_by_entity(
                    eid,
                    profile_id,
                    include_global=include_global,
                    include_shared=include_shared,
                ):
                    if hop_decay > activation.get(fact.fact_id, 0.0):
                        activation[fact.fact_id] = hop_decay
                        next_frontier.add(fact.fact_id)
            frontier = next_frontier
            if not frontier:
                break

        return activation

    def score_candidates(
        self,
        query: str,
        candidate_fact_ids: list[str],
        profile_id: str,
        *,
        include_global: bool | None = None,
        include_shared: bool | None = None,
    ) -> dict[str, float]:
        """Serialize access to the scope-keyed graph/entity cache."""
        with self._cache_lock:
            return self._score_candidates_locked(
                query,
                candidate_fact_ids,
                profile_id,
                include_global=include_global,
                include_shared=include_shared,
            )

    def _score_candidates_locked(
        self,
        query: str,
        candidate_fact_ids: list[str],
        profile_id: str,
        *,
        include_global: bool | None = None,
        include_shared: bool | None = None,
    ) -> dict[str, float]:
        """Score candidate facts by their entity-graph proximity to query entities.

        V3.4.11 "Signal Enhancer" architecture: instead of returning its own
        independent set of fact_ids (which get outranked by multi-channel facts
        in RRF), this method scores EXISTING candidates from semantic/BM25
        by their graph connectivity to query entities.

        Research basis: Microsoft GraphRAG DRIFT Search, HippoRAG, Pistis-RAG
        cascaded architecture. Graph signals act as post-retrieval boosters,
        not independent retrievers. Avoids the "weakest link" phenomenon where
        non-overlapping result sets cause rank collapse in RRF fusion.

        Args:
            query: The user's query string.
            candidate_fact_ids: Fact IDs from semantic/BM25/other channels.
            profile_id: User profile.

        Returns:
            Dict mapping fact_id → entity_graph score [0, 1].
            Facts with no entity connection return 0.
            Facts directly linked to query entities score ~1.0.
            Facts 1-hop away score ~0.7 (decay factor).
        """
        if not candidate_fact_ids:
            return {}

        if include_global is None:
            include_global = bool(getattr(self, "include_global", False))
        if include_shared is None:
            include_shared = bool(getattr(self, "include_shared", False))
        allowed_candidates = authorized_fact_ids(
            self._db,
            candidate_fact_ids,
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
        )
        if not allowed_candidates:
            return {}

        raw_entities, plain_words = extract_query_entities(query), query_plain_words(query)
        if not raw_entities and not plain_words:
            return {}

        self._ensure_adjacency(
            profile_id,
            include_global=include_global,
            include_shared=include_shared,
        )
        canonical_ids = self._resolve_entities(raw_entities, profile_id, plain_words)
        if not canonical_ids:
            return {}

        # The same walk as search(), over the same snapshot. This method used
        # to carry its own copy of the loop, which is how the two drifted: the
        # community bias here has never applied search()'s outsider penalty, and
        # the only record of that was the absence of six lines.
        snapshot = getattr(self, "_snapshot", None)
        if snapshot is None or not snapshot.node_count:
            return {}
        activation_result = spreading.activate(
            snapshot,
            canonical_ids,
            decay=self._decay,
            threshold=self._threshold,
            max_hops=self._max_hops,
        )
        spreading.apply_community_bias(
            activation_result.scores,
            snapshot,
            canonical_ids,
            # Re-scoring another channel's candidates, so a fact outside every
            # seed community is not damped -- see apply_community_bias.
            penalise_outsiders=False,
        )
        # Scores ONLY for the candidate set (<= 100), normalized to [0, 1]: a
        # dict over every node of the graph was built here and then read for
        # those few ids.
        scores, node_index = activation_result.scores, snapshot.node_index
        candidate_set = allowed_candidates
        scored = {fid: (float(scores[node_index[fid]]) if fid in node_index else 0.0)
                  for fid in candidate_set}

        max_score = max(scored.values()) if scored else 0
        if max_score > 0:
            scored = {fid: sc / max_score for fid, sc in scored.items()}

        return scored

    def _suppress_contradictions(
        self,
        activation: dict[str, float],
        profile_id: str,
    ) -> None:
        """P3: Penalize the older fact in a contradiction pair.

        4.1.18: ``supersedes`` edges no longer lower activation. The penalty
        multiplied the edge's SOURCE by 0.3 as "the replaced fact", but the
        source is the NEWER fact everywhere else (``EdgeType.SUPERSEDES`` is
        documented "Newer fact replaces older"; the sheaf check writes source
        = the fact being stored) — so the current memory was the one
        suppressed, on 4,481 of 4,786 edges in a real store. The edges
        themselves come only from the sheaf consistency check, which flags
        geometric disagreement between facts that share an entity rather than
        one fact making another untrue: 0 of 34 sampled were supersessions.
        Reversing the direction would just suppress the older facts instead.
        The edges stay in the graph; they are not a ranking signal.
        """
        candidate_ids = list(activation.keys())
        if not candidate_ids:
            return
        try:
            placeholders = ",".join("?" * len(candidate_ids))
            sql = (
                "SELECT source_id, target_id, edge_type FROM graph_edges "
                "WHERE profile_id = ? AND edge_type = 'contradiction' "
                "AND (source_id IN (" + placeholders + ") "
                "OR target_id IN (" + placeholders + "))"
            )
            rows = self._db.execute(sql, (profile_id, *candidate_ids, *candidate_ids))
            edges = [dict(r) for r in rows]
            if not edges:
                return

            # Batch load created_at for involved facts
            involved = set()
            for e in edges:
                involved.add(e["source_id"])
                involved.add(e["target_id"])
            involved = involved & set(candidate_ids)
            if not involved:
                return
            ph2 = ",".join("?" * len(involved))
            ts_rows = self._db.execute(
                "SELECT fact_id, created_at FROM atomic_facts "
                "WHERE fact_id IN (" + ph2 + ") AND profile_id = ?",
                (*involved, profile_id),
            )
            ts_map = {dict(r)["fact_id"]: dict(r).get("created_at", "") for r in ts_rows}

            for e in edges:
                src, tgt, etype = e["source_id"], e["target_id"], e["edge_type"]
                if etype == "contradiction":
                    src_ts = ts_map.get(src, "")
                    tgt_ts = ts_map.get(tgt, "")
                    if src_ts and tgt_ts:
                        older = src if src_ts < tgt_ts else tgt
                        if older in activation:
                            activation[older] *= 0.5
        except Exception as exc:
            logger.debug("Contradiction suppression failed: %s", exc)

    def _resolve_entities(self, raw: list[str], profile_id: str,
                          plain_words: list[str] | None = None) -> list[str]:
        """Resolve local and visible cross-profile canonical entity IDs."""
        ids: list[str] = []
        seen: set[str] = set()
        # A question reads the store: lookup() never creates entities, saves
        # aliases, or asks the language model (resolve() is for saving).
        lookup = getattr(self._resolver, "lookup", None)
        if lookup is not None:
            found = list(lookup(raw, profile_id).values())
            plain = getattr(self._resolver, "lookup_plain_words", None)
            if plain is not None and plain_words:
                found += list(plain(plain_words, profile_id).values())
            for eid in found:
                if eid not in seen:
                    seen.add(eid)
                    ids.append(eid)
        else:
            for name in raw:
                ent = self._db.get_entity_by_name(name, profile_id)
                if ent and ent.entity_id not in seen:
                    seen.add(ent.entity_id)
                    ids.append(ent.entity_id)

        # Entity UUIDs are profile-local. Supplement local resolution with
        # same-name/alias IDs that are actually referenced by visible facts.
        names = [name.strip().lower() for name in raw if name.strip()]
        if names and self._visible_fact_ids and self._entity_to_facts:
            placeholders = ",".join("?" for _ in names)
            try:
                rows = self._db.execute(
                    "SELECT entity_id, '' AS source, '' AS alias, '' AS canonical_name "
                    "FROM canonical_entities "
                    f"WHERE canonical_name COLLATE NOCASE IN ({placeholders}) "
                    "UNION SELECT ea.entity_id, ea.source, ea.alias, ce.canonical_name "
                    "FROM entity_aliases ea JOIN canonical_entities ce USING (entity_id) "
                    f"WHERE ea.alias COLLATE NOCASE IN ({placeholders})",
                    (*names, *names),
                )
                rows = [r for r in map(dict, rows) if _alias_in_use(
                    r["source"], r["alias"], r["canonical_name"])]
            except Exception:
                rows = []
            for row in rows:
                entity_id = str(dict(row)["entity_id"])
                if entity_id in self._entity_to_facts and entity_id not in seen:
                    seen.add(entity_id)
                    ids.append(entity_id)
        return ids

    def _discover_entities(
        self,
        fact_ids: set[str],
        profile_id: str,
        visited: set[str],
    ) -> list[str]:
        """Find new canonical entity IDs referenced by a set of facts."""
        new: list[str] = []
        seen = set(visited)
        allowed_fact_ids = authorized_fact_ids(
            self._db,
            fact_ids,
            profile_id,
            include_global=bool(getattr(self, "include_global", False)),
            include_shared=bool(getattr(self, "include_shared", False)),
        )
        for fid in allowed_fact_ids:
            rows = self._db.execute(
                "SELECT canonical_entities_json FROM atomic_facts WHERE fact_id = ?",
                (fid,),
            )
            if not rows:
                continue
            raw = dict(rows[0]).get("canonical_entities_json")
            if not raw:
                continue
            try:
                for eid in json.loads(raw):
                    if eid not in seen:
                        seen.add(eid)
                        new.append(eid)
            except (ValueError, TypeError):
                continue
        return new

    # v3.4.5: CozoDB-backed search (Sprint 2)
