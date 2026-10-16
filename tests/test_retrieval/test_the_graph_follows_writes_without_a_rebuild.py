# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3

"""A write changes the entity graph by its own size, never by the store's.

N-11 (live, 2026-10-11): every write made the next recall reload the entity
maps, the graph metrics and the adjacency cache -- 25.6k nodes, 611k edges,
2.3 s quiet and 5+ s under load -- and while Mode B enrichment wrote every few
seconds nearly every recall paid it. These tests pin the replacement contract
(retrieval/graph_catchup, retrieval/graph_delta):

* a recall after a write applies that write's rows to the cached graph and
  never reloads the whole graph, in the recall or beside it;
* a deleted, erased, withheld or re-scoped memory is removed before the walk
  runs, so a stale graph can never return it or carry activation through it;
* when a backlog is too large to apply on a recall's clock, removals are still
  applied at once, the additions are left to one background rebuild, and the
  recall says how many seconds its graph is behind;
* a writer and several readers running at once see no exception and no torn
  snapshot.

Real ``EntityGraphChannel`` over a real SQLite store; no mocks of the code
under test.
"""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest

from superlocalmemory.retrieval import graph_catchup
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager

P = "default"
SCOPE = (P, False, False)
NAMES = ("Alice", "Bob", "Carol", "Dave", "Erin")


def _ent(name: str) -> str:
    return f"ent_{name.lower()}"


def _insert_fact(conn, fid: str, entities: tuple[str, ...] = ()) -> None:
    conn.execute("INSERT INTO memories (memory_id, profile_id, scope, content)"
                 " VALUES (?, ?, 'personal', 'x')", (f"m_{fid}", P))
    conn.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, scope, content,"
        " fact_type, confidence, importance, evidence_count, access_count,"
        " canonical_entities_json, created_at) VALUES (?, ?, ?, 'personal', ?,"
        " 'semantic', 0.9, 0.5, 1, 0, ?, datetime('now'))",
        (fid, f"m_{fid}", P, f"fact {fid}", json.dumps([_ent(n) for n in entities])))


def _insert_edge(conn, eid: str, src: str, dst: str, weight: float = 0.9) -> None:
    conn.execute(
        "INSERT INTO graph_edges (edge_id, profile_id, source_id, target_id, edge_type,"
        " weight) VALUES (?, ?, ?, ?, 'entity', ?)", (eid, P, src, dst, weight))


@pytest.fixture()
def db(tmp_path) -> DatabaseManager:
    """A0 (Alice) -- BRIDGE -- TARGET; ring of 30 Bob/Carol facts besides."""
    store = DatabaseManager(tmp_path / "graph.db")
    with store.raw_connection() as conn:
        schema.create_all_tables(conn)
        # Soft delete arrives with a migration (M011) on a real store; the
        # change log watches it from the next start, as it does there.
        conn.execute("ALTER TABLE atomic_facts ADD COLUMN archive_status TEXT")
        schema.create_all_tables(conn)
        conn.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (P, P))
        for name in NAMES:
            conn.execute("INSERT INTO canonical_entities (entity_id, profile_id,"
                         " canonical_name, entity_type) VALUES (?, ?, ?, 'person')",
                         (_ent(name), P, name))
        _insert_fact(conn, "A0", ("Alice",))
        _insert_fact(conn, "BRIDGE")
        _insert_fact(conn, "TARGET")
        _insert_edge(conn, "e_a0_bridge", "A0", "BRIDGE")
        _insert_edge(conn, "e_bridge_target", "BRIDGE", "TARGET")
        for i in range(30):
            _insert_fact(conn, f"R{i:02d}", (NAMES[1 + i % 2],))
            _insert_edge(conn, f"e_ring{i}", f"R{i:02d}", f"R{(i + 1) % 30:02d}", 0.5)
        conn.commit()
    return store


def _channel(db: DatabaseManager) -> EntityGraphChannel:
    channel = EntityGraphChannel(db, None)
    channel._ensure_adjacency(P)
    return channel


def _count_loads(monkeypatch) -> dict[str, int]:
    loads = {"n": 0}
    real = EntityGraphChannel._load_adjacency_from_db

    def counting(self, *args, **kwargs):
        loads["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(EntityGraphChannel, "_load_adjacency_from_db", counting)
    return loads


def _graph_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("slm-graph")]


def _neighbours(channel: EntityGraphChannel, fid: str) -> set[str]:
    snap = channel._adj_slots[SCOPE].snapshot
    i = snap.node_index[fid]
    return {snap.node_ids[j] for j in snap.indices[snap.indptr[i]:snap.indptr[i + 1]]}


# -- no reload on the recall path after a write ------------------------------

def test_a_write_reaches_the_graph_without_any_full_reload(db, monkeypatch) -> None:
    channel = _channel(db)
    loads = _count_loads(monkeypatch)
    with db.raw_connection() as conn:
        _insert_fact(conn, "NEW", ("Alice", "Dave"))
        _insert_edge(conn, "e_new_target", "NEW", "TARGET", 0.8)
        conn.commit()

    channel._ensure_adjacency(P)

    assert loads["n"] == 0, "a write must not reload the whole graph"
    assert _graph_threads() == [], "nor rebuild it beside the recall"
    assert "NEW" in channel._visible_fact_ids
    assert "TARGET" in _neighbours(channel, "NEW") and "NEW" in _neighbours(channel, "TARGET")
    snap = channel._adj_slots[SCOPE].snapshot
    assert snap.node_index["NEW"] in snap.entity_to_facts[_ent("Dave")]
    assert channel.graph_lag_s(P) == 0.0


def test_an_entity_link_added_to_an_old_fact_is_followed(db, monkeypatch) -> None:
    channel = _channel(db)
    loads = _count_loads(monkeypatch)
    db.execute("UPDATE atomic_facts SET canonical_entities_json = ? WHERE fact_id = ?",
               (json.dumps([_ent("Erin")]), "TARGET"))
    channel._ensure_adjacency(P)
    snap = channel._adj_slots[SCOPE].snapshot
    assert loads["n"] == 0
    assert snap.entity_to_facts[_ent("Erin")] == (snap.node_index["TARGET"],)
    assert snap.fact_to_entities[snap.node_index["TARGET"]] == (_ent("Erin"),)


def test_many_writes_never_reload_and_match_a_fresh_build(db, monkeypatch) -> None:
    channel = _channel(db)
    loads = _count_loads(monkeypatch)
    for k in range(25):
        with db.raw_connection() as conn:
            _insert_fact(conn, f"W{k:02d}", (NAMES[k % 5],))
            _insert_edge(conn, f"e_w{k}", f"W{k:02d}", f"R{k:02d}", 0.7)
            if k % 5 == 4:
                conn.execute("DELETE FROM atomic_facts WHERE fact_id = ?", (f"R{k - 1:02d}",))
            conn.commit()
        channel.score_candidates("Alice met Bob", ["A0", "R00"], P)
    assert loads["n"] == 0

    fresh = EntityGraphChannel(db, None)
    fresh._ensure_adjacency(P)
    got, want = channel._adj_slots[SCOPE].snapshot, fresh._adj_slots[SCOPE].snapshot
    assert set(got.node_ids) == set(want.node_ids)
    for fid in want.node_ids:
        assert _neighbours(channel, fid) == _neighbours(fresh, fid), fid
    assert {k: {got.node_ids[i] for i in v} for k, v in got.entity_to_facts.items()} == \
        {k: {want.node_ids[i] for i in v} for k, v in want.entity_to_facts.items()}
    query = "What did Alice tell Carol and Bob?"
    assert channel.search(query, P) == fresh.search(query, P)


# -- removals are applied before the walk, always ----------------------------

@pytest.mark.parametrize("remove", [
    "DELETE FROM atomic_facts WHERE fact_id = 'BRIDGE'",
    "UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = 'BRIDGE'",
    "UPDATE atomic_facts SET archive_status = 'archived' WHERE fact_id = 'BRIDGE'",
    "UPDATE atomic_facts SET profile_id = 'elsewhere' WHERE fact_id = 'BRIDGE'",
], ids=["deleted", "withheld", "soft_deleted", "moved_profile"])
def test_a_removed_fact_never_comes_back_through_the_cached_graph(db, remove) -> None:
    channel = _channel(db)
    query = "What does Alice know?"
    assert {"BRIDGE", "TARGET"} <= {f for f, _ in channel.search(query, P)}
    if "elsewhere" in remove:
        db.execute("INSERT OR IGNORE INTO profiles (profile_id, name) VALUES ('elsewhere', 'e')")
    db.execute(remove)

    found = {f for f, _ in channel.search(query, P)}
    assert "BRIDGE" not in found
    # TARGET was reachable only through BRIDGE: no activation may pass it.
    assert "TARGET" not in found
    assert channel.score_candidates(query, ["TARGET", "BRIDGE"], P) in (
        {}, {"TARGET": 0.0})
    assert "BRIDGE" not in channel._visible_fact_ids


def test_removals_apply_even_when_the_backlog_is_deferred(db, monkeypatch) -> None:
    channel = _channel(db)
    monkeypatch.setenv("SLM_GRAPH_DELTA_MAX_FACTS", "0")  # every addition deferred
    monkeypatch.setattr(graph_catchup, "request_compaction", lambda *a, **k: False)
    with db.raw_connection() as conn:
        _insert_fact(conn, "LATE", ("Alice",))
        conn.execute("DELETE FROM atomic_facts WHERE fact_id = 'BRIDGE'")
        conn.commit()
    found = {f for f, _ in channel.search("What does Alice know?", P)}
    assert "BRIDGE" not in found and "TARGET" not in found
    assert "LATE" not in channel._visible_fact_ids  # deferred, not lost
    assert channel.graph_lag_s(P) is not None and channel.graph_lag_s(P) >= 0.0


# -- full rebuilds only beside recall, and only when something needs one ------

def _spy_compactions(monkeypatch) -> list[str]:
    reasons: list[str] = []
    real = graph_catchup.request_compaction

    def spy(channel, scope_key, *, reason=""):
        reasons.append(reason)
        return real(channel, scope_key, reason=reason)

    monkeypatch.setattr(graph_catchup, "request_compaction", spy)
    return reasons


def test_a_delete_is_spliced_and_never_triggers_a_rebuild(db, monkeypatch) -> None:
    monkeypatch.setenv("SLM_GRAPH_METRICS_CHECK_S", "0")  # checked on every recall
    db.execute("INSERT INTO fact_importance (fact_id, profile_id, pagerank_score,"
               " community_id) VALUES ('R05', ?, 0.01, 1)", (P,))
    channel = _channel(db)
    reasons = _spy_compactions(monkeypatch)
    db.execute("DELETE FROM fact_importance WHERE fact_id = 'R05'")
    db.execute("DELETE FROM atomic_facts WHERE fact_id = 'R05'")
    channel._ensure_adjacency(P)
    assert "R05" not in channel._visible_fact_ids
    assert reasons == [] and _graph_threads() == []


def test_recomputed_graph_metrics_are_picked_up_beside_recall(db, monkeypatch) -> None:
    monkeypatch.setenv("SLM_GRAPH_METRICS_CHECK_S", "0")
    channel = _channel(db)
    reasons = _spy_compactions(monkeypatch)
    db.execute("INSERT INTO fact_importance (fact_id, profile_id, pagerank_score,"
               " community_id, computed_at) VALUES ('A0', ?, 0.2, 7, '2999-01-01')", (P,))
    channel._ensure_adjacency(P)
    assert reasons == ["graph metrics"]
    assert graph_catchup.wait_idle(channel, timeout=20)
    channel._ensure_adjacency(P)
    snap = channel._adj_slots[SCOPE].snapshot
    assert snap.pagerank[snap.node_index["A0"]] == pytest.approx(0.2)
    assert snap.community[snap.node_index["A0"]] == 7


# -- the recall says how far behind its graph is ------------------------------

def test_a_deferred_backlog_reports_lag_until_the_rebuild_lands(db, monkeypatch) -> None:
    channel = _channel(db)
    monkeypatch.setenv("SLM_GRAPH_DELTA_MAX_FACTS", "0")
    inside, release = threading.Event(), threading.Event()
    real = EntityGraphChannel._load_adjacency_from_db

    def held(self, *args, **kwargs):  # only the background rebuild loads now
        inside.set()
        assert release.wait(20), "test never released the rebuild"
        return real(self, *args, **kwargs)

    monkeypatch.setattr(EntityGraphChannel, "_load_adjacency_from_db", held)
    with db.raw_connection() as conn:
        _insert_fact(conn, "LATE", ("Alice",))
        conn.commit()
    started = time.monotonic()
    channel._ensure_adjacency(P)
    assert time.monotonic() - started < 1.0          # the recall did not wait
    assert inside.wait(10), "one background rebuild must start"
    lag = channel.graph_lag_s(P)
    assert lag is not None and "LATE" not in channel._visible_fact_ids
    time.sleep(0.05)
    assert channel.graph_lag_s(P) > lag              # it grows while behind

    release.set()
    assert graph_catchup.wait_idle(channel, timeout=20)
    channel._ensure_adjacency(P)
    assert "LATE" in channel._visible_fact_ids
    assert channel.graph_lag_s(P) == 0.0


def test_the_engine_puts_the_graph_lag_on_the_response(db) -> None:
    from superlocalmemory.server.recall_serializer import recall_response_metadata
    from superlocalmemory.storage.models import RecallResponse

    response = RecallResponse(query="q", mode="a", results=[], query_type="general",
                              channel_weights={}, total_candidates=0,
                              retrieval_time_ms=1.0, graph_lag_s=2.5)
    assert recall_response_metadata(response)["graph_lag_s"] == 2.5
    assert recall_response_metadata(RecallResponse(
        query="q", mode="a", results=[], query_type="general", channel_weights={},
        total_candidates=0, retrieval_time_ms=1.0))["graph_lag_s"] is None


# -- one writer, several readers ----------------------------------------------

def _check_snapshot(snap) -> None:
    n = snap.node_count
    assert len(snap.node_index) == n and snap.indptr.shape == (n + 1,)
    assert np.all(np.diff(snap.indptr) >= 0) and snap.indptr[-1] == snap.indices.size
    assert snap.indices.size == snap.weights.size
    assert snap.indices.size == 0 or (snap.indices.min() >= 0 and snap.indices.max() < n)
    assert len(snap.fact_to_entities) == n and snap.pagerank.shape == (n,)
    for idxs in snap.entity_to_facts.values():
        assert all(0 <= i < n for i in idxs)


def test_a_writer_and_readers_together_see_no_error_and_no_torn_graph(db) -> None:
    channel = _channel(db)
    stop, errors, deleted = threading.Event(), [], set()
    lock = threading.Lock()

    def writer() -> None:
        k = 0
        while not stop.is_set():
            try:
                with db.raw_connection() as conn:
                    _insert_fact(conn, f"C{k:04d}", (NAMES[k % 5],))
                    _insert_edge(conn, f"e_c{k}", f"C{k:04d}", f"R{k % 30:02d}")
                    if k % 4 == 3:
                        conn.execute("DELETE FROM atomic_facts WHERE fact_id = ?",
                                     (f"C{k - 2:04d}",))
                    conn.commit()
                if k % 4 == 3:
                    with lock:
                        deleted.add(f"C{k - 2:04d}")
                k += 1
            except Exception as exc:  # noqa: BLE001 -- collected and asserted
                errors.append(repr(exc))
            time.sleep(0.005)

    def reader() -> None:
        while not stop.is_set():
            try:
                with lock:
                    gone = set(deleted)
                found = {f for f, _ in channel.search("Alice Bob Carol Dave Erin", P)}
                assert not (found & gone), found & gone
                channel.score_candidates("Bob and Erin", sorted(gone)[:5] + ["R01"], P)
                with channel._cache_lock:
                    _check_snapshot(channel._adj_slots[SCOPE].snapshot)
            except Exception as exc:  # noqa: BLE001 -- collected and asserted
                errors.append(repr(exc))

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(2.0)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors[:5]
    assert graph_catchup.wait_idle(channel, timeout=20)
