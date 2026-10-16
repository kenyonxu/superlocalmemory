# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""A grown graph is followed, never rebuilt; one that lost a memory is not served stale.

On a store still being enriched the edge and fact counts move constantly, and
every recall that noticed rebuilt the whole in-memory graph: first on its own
clock (15 rebuilds in 60 recalls on a 481,000-edge store, 1.5-13 s each), then
(4.1.22) beside it -- which still cost a full rebuild per write (N-11). Now a
write is spliced in by its own size (retrieval/graph_catchup); the full
contract is in test_the_graph_follows_writes_without_a_rebuild.py.
"""

from __future__ import annotations

import time

import pytest

from superlocalmemory.retrieval import graph_catchup
from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from tests.test_retrieval.cross_scope_fixture import REQ, build_store


@pytest.fixture()
def store(tmp_path):
    return build_store(tmp_path / "g.db", n_global=0, n_shared=0, n_denied=0)


def _add_fact(store, fid: str) -> None:
    with store.db.raw_connection() as conn:
        conn.execute("INSERT INTO memories (memory_id, profile_id, scope, content)"
                     " VALUES (?, ?, 'personal', 'x')", (f"m_{fid}", REQ))
        conn.execute(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, scope, content,"
            " fact_type, confidence, importance, evidence_count, access_count, created_at)"
            " VALUES (?, ?, ?, 'personal', 'new', 'semantic', 0.9, 0.5, 1, 0, datetime('now'))",
            (fid, f"m_{fid}", REQ))


def test_a_grown_graph_is_followed_on_the_recall_without_a_rebuild(store, monkeypatch) -> None:
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    loads = {"n": 0}
    real = EntityGraphChannel._load_adjacency_from_db

    def slow_load(self, *a, **k):
        loads["n"] += 1
        time.sleep(0.5)
        return real(self, *a, **k)

    monkeypatch.setattr(EntityGraphChannel, "_load_adjacency_from_db", slow_load)
    _add_fact(store, "LNEW")
    started = time.monotonic()
    ch._ensure_adjacency(REQ)
    assert time.monotonic() - started < 0.4          # the recall did not wait
    assert "LNEW" in ch._visible_fact_ids            # and the new memory is in
    assert loads["n"] == 0                           # with no rebuild anywhere
    assert graph_catchup.wait_idle(ch, timeout=5)


def test_a_withheld_memory_is_never_served_from_the_cached_graph(store) -> None:
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    gone = store.ids("L")[0]
    assert gone in ch._visible_fact_ids
    store.db.execute("UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = ?", (gone,))
    ch._ensure_adjacency(REQ)
    assert gone not in ch._visible_fact_ids          # removed before any walk


def test_without_the_change_log_the_rebuild_stays_synchronous(store) -> None:
    from superlocalmemory.storage import fact_search_changes as changes

    for trigger in changes.trigger_names():
        store.db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    store.db.execute(f"DROP TABLE {changes.TABLE}")
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    assert ch._adj_slots[(REQ, False, False)].seq is None   # nothing to follow
    _add_fact(store, "LSYNC")
    ch._ensure_adjacency(REQ)
    assert "LSYNC" in ch._visible_fact_ids


def test_a_log_that_no_longer_reaches_the_slot_forces_a_rebuild(store, monkeypatch) -> None:
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    slot = ch._adj_slots[(REQ, False, False)]
    _add_fact(store, "LGAP")
    # The log kept only rows past the slot: whatever was pruned is unknowable.
    store.db.execute("DELETE FROM fact_search_changes WHERE seq <= ?", (slot.seq + 1,))
    _add_fact(store, "LGAP2")
    assert graph_catchup.catch_up(ch, (REQ, False, False), slot, now=time.monotonic()) is None
    ch._ensure_adjacency(REQ)                         # rebuilt, synchronously here
    assert {"LGAP", "LGAP2"} <= set(ch._visible_fact_ids)


def test_a_replaced_store_file_is_never_followed(store, monkeypatch) -> None:
    """A restore swaps memory.db: its change log is another log, prove nothing."""
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    slot = ch._adj_slots[(REQ, False, False)]
    assert slot.store_id is not None
    monkeypatch.setattr(graph_catchup, "_store_identity", lambda db: (0, 0))
    assert graph_catchup.catch_up(ch, (REQ, False, False), slot, now=time.monotonic()) is None
