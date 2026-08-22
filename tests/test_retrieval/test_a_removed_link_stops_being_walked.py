"""A link removed from the store stops being followed during a search.

The graph is held in two stores. SQLite is the record; a second store holds a
projection of it that the search walks, because reading the projection takes
395 ms on a 208,000-edge graph where reading SQLite takes 2,477 ms.

Nothing spans a transaction across the two. So when the tidy-up pass removes a
link from the record, the projection has to be told, and until it has been told
the search must read the record directly. Both halves are here: the tidy-up
pass leaves a durable note, and the search declines the projection while any
note is outstanding.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

_NOW = "2026-08-23T00:00:00Z"


def _make_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    from superlocalmemory.core.config import SLMConfig
    from superlocalmemory.infra.data_root import state_path
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager
    from superlocalmemory.storage.migration_runner import apply_all

    config = SLMConfig.load()
    db = DatabaseManager(config.db_path)
    db.initialize(schema)
    apply_all(pathlib.Path(state_path("learning.db")), pathlib.Path(config.db_path))
    return db, config


def _add_fact(db, fact_id, profile_id="default"):
    db.execute(
        "INSERT INTO memories (memory_id, profile_id, scope, content, session_id, "
        "speaker, role, created_at, metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"mem-{fact_id}", profile_id, "personal", f"content for {fact_id}",
         "s1", "user", "user", _NOW, "{}"),
    )
    db.execute(
        "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, scope, content, "
        "fact_type, entities_json, canonical_entities_json, confidence, importance, "
        "evidence_count, access_count, pinned, source_turn_ids_json, session_id, "
        "fisher_last_applied_access, lifecycle, quarantined, emotional_valence, "
        "emotional_arousal, signal_type, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fact_id, f"mem-{fact_id}", profile_id, "personal", f"content for {fact_id}",
         "semantic", "[]", "[]", 0.9, 0.5, 1, 0, 0, "[]", "s1", 0, "active", 0,
         0.0, 0.0, "factual", _NOW),
    )


def _add_edge(db, edge_id, source, target, profile_id="default", weight=0.9):
    db.execute(
        "INSERT INTO graph_edges (edge_id, profile_id, source_id, target_id, "
        "edge_type, weight, created_at) VALUES (?,?,?,?,?,?,?)",
        (edge_id, profile_id, source, target, "semantic", weight, _NOW),
    )


def test_removing_a_link_leaves_a_note_naming_the_facts_it_touched(
    tmp_path, monkeypatch,
):
    """The note is what makes the removal survive a crash before it propagates."""
    db, _config = _make_store(tmp_path, monkeypatch)
    with db.transaction():
        for index in range(4):
            _add_fact(db, f"fact{index:02d}")
        _add_edge(db, "keep", "fact00", "fact01")
        _add_edge(db, "loop", "fact03", "fact03")   # a self-link, always removed

    assert db.execute("SELECT fact_id FROM projection_outbox") == []

    from superlocalmemory.core.graph_pruner import prune_graph

    stats = prune_graph(db, "default")
    assert stats["self_loops_removed"] == 1

    queued = {dict(row)["fact_id"] for row in db.execute(
        "SELECT fact_id FROM projection_outbox"
    )}
    assert "fact03" in queued, (
        "the fact whose link was removed was not queued, so the second store "
        "would keep serving a link this pass deleted"
    )
    # An untouched fact is not re-projected for nothing.
    assert "fact00" not in queued


def test_the_search_declines_the_projection_while_a_note_is_outstanding(
    tmp_path, monkeypatch,
):
    """Behind is behind. The record answers until the projection catches up."""
    db, _config = _make_store(tmp_path, monkeypatch)
    from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
    from superlocalmemory.storage import projection_outbox

    channel = EntityGraphChannel.__new__(EntityGraphChannel)
    channel._db = db

    assert channel._projection_is_caught_up("default") is True

    projection_outbox.enqueue(db, "any-fact", "default")
    assert channel._projection_is_caught_up("default") is False
    # One workspace being behind says nothing about another.
    assert channel._projection_is_caught_up("other") is True

    db.execute("DELETE FROM projection_outbox")
    assert channel._projection_is_caught_up("default") is True


def test_a_store_with_no_queue_at_all_still_uses_the_projection(
    tmp_path, monkeypatch,
):
    """The control. An older store has no queue, and must not lose the fast path."""
    db, config = _make_store(tmp_path, monkeypatch)
    from superlocalmemory.retrieval.entity_channel import EntityGraphChannel
    from superlocalmemory.storage import projection_outbox

    raw = sqlite3.connect(str(config.db_path))
    raw.execute("DROP TABLE projection_outbox")
    raw.commit()
    raw.close()
    projection_outbox.is_available.cache_clear() if hasattr(
        projection_outbox.is_available, "cache_clear"
    ) else None
    try:
        delattr(db, "_slm_outbox_available")
    except AttributeError:
        pass

    channel = EntityGraphChannel.__new__(EntityGraphChannel)
    channel._db = db
    assert channel._projection_is_caught_up("default") is True


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("pycozo") is None,
    reason="the graph projection needs its engine installed",
)
def test_a_removed_link_is_gone_from_the_projection_once_the_note_is_applied(
    tmp_path, monkeypatch,
):
    """End to end, against a real projection rather than a stand-in."""
    db, config = _make_store(tmp_path, monkeypatch)
    with db.transaction():
        for index in range(4):
            _add_fact(db, f"fact{index:02d}")
        _add_edge(db, "keep", "fact00", "fact01")
        _add_edge(db, "loop", "fact03", "fact03")

    from superlocalmemory.core.graph_pruner import prune_graph
    from superlocalmemory.core.projection_drain import ProjectionDrain
    from superlocalmemory.graph.cozo_backend import CozoDBGraphBackend

    backend = CozoDBGraphBackend(str(tmp_path / "graph.cozo"))
    source = sqlite3.connect(str(config.db_path))
    source.row_factory = sqlite3.Row
    backend.bulk_import_from_sqlite(source, "default")
    source.close()

    def projected():
        rows = backend._db.run(
            "?[a, b] := *edge{from_id: a, to_id: b, profile_id: $pid}",
            {"pid": "default"},
        )
        return {tuple(row) for row in rows.values.tolist()}

    assert ("fact03", "fact03") in projected()

    prune_graph(db, "default")
    ProjectionDrain(db, lambda: backend, lambda: None).drain_once(limit=100)

    remaining = projected()
    assert ("fact03", "fact03") not in remaining, (
        "the removed link is still in the projection, so a search would still "
        "follow it"
    )
    assert ("fact00", "fact01") in remaining, (
        "re-projecting must not drop the links that were never removed"
    )
