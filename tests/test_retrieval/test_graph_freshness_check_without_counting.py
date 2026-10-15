# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Recall learns its cached graph is current without counting the store.

Every recall counted every edge and fact of its scope to decide whether the
cached entity graph was still current: 44 ms per recall on a 481,000-edge
store, almost always to learn that nothing had changed. The store's change log
(storage/fact_search_changes) and the edge rowid now say what changed, if
anything, so a recall counts nothing -- and any commit, from any connection, is
still seen (retrieval/graph_catchup).
"""

from __future__ import annotations

import sqlite3

import pytest

from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
from superlocalmemory.storage import store_signature
from tests.test_retrieval.cross_scope_fixture import REQ, build_store
from tests.test_retrieval.test_the_graph_is_rebuilt_beside_recall import _add_fact


@pytest.fixture()
def store(tmp_path):
    return build_store(tmp_path / "g.db", n_global=0, n_shared=0, n_denied=0)


def _counting(ch, monkeypatch) -> dict:
    calls = {"edges": 0, "facts": 0}
    real_edges, real_facts = ch._get_edge_count, ch._db.get_fact_count

    def edges(*a, **k):
        calls["edges"] += 1
        return real_edges(*a, **k)

    def facts(*a, **k):
        calls["facts"] += 1
        return real_facts(*a, **k)

    monkeypatch.setattr(ch, "_get_edge_count", edges)
    monkeypatch.setattr(ch._db, "get_fact_count", facts)
    return calls


def test_an_unchanged_store_is_not_counted_again(store, monkeypatch) -> None:
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    calls = _counting(ch, monkeypatch)
    for _ in range(5):
        ch._ensure_adjacency(REQ)
    assert calls == {"edges": 0, "facts": 0}


def test_any_commit_from_another_connection_is_seen_without_counting(store, monkeypatch) -> None:
    ch = EntityGraphChannel(store.db, None)
    ch._ensure_adjacency(REQ)
    calls = _counting(ch, monkeypatch)
    other = sqlite3.connect(store.db.db_path)
    other.execute("UPDATE atomic_facts SET importance = importance WHERE 0")  # no change
    other.commit()
    _add_fact(store, "LNEW2")  # a real commit through the store's own connections
    other.close()
    ch._ensure_adjacency(REQ)
    assert calls == {"edges": 0, "facts": 0}
    assert "LNEW2" in ch._visible_fact_ids


def test_an_unreadable_store_path_is_never_trusted(tmp_path) -> None:
    assert store_signature.of(tmp_path / "missing.db") is None
    assert store_signature.of(None) is None
