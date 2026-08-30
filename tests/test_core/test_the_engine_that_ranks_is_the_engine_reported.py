"""Whichever engine computes the ranking is the one the report names.

Two engines can produce this ranking: an in-process one that reads SQLite, and
a graph engine that reads the projection. They must be interchangeable, and the
report has to say which one ran — otherwise a store silently switches engines
and every log line, note and measurement afterwards is about the other one.

The report is also the only place a reader can see that the projection was
declined, so the reason has to survive into it.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

_NOW = "2026-08-23T00:00:00Z"
_pycozo = __import__("importlib.util", fromlist=["util"]).find_spec("pycozo")


def _store(tmp_path, monkeypatch):
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


def _ring(db, size=12):
    """A cycle: every fact has one in-edge and one out-edge."""
    with db.transaction():
        for index in range(size):
            fact_id = f"f{index:02d}"
            db.execute(
                "INSERT INTO memories (memory_id, profile_id, scope, content, "
                "session_id, speaker, role, created_at, metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"mem-{fact_id}", "default", "personal", f"content {fact_id}",
                 "s1", "user", "user", _NOW, "{}"),
            )
            db.execute(
                "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, scope, "
                "content, fact_type, entities_json, canonical_entities_json, "
                "confidence, importance, evidence_count, access_count, pinned, "
                "source_turn_ids_json, session_id, fisher_last_applied_access, "
                "lifecycle, quarantined, emotional_valence, emotional_arousal, "
                "signal_type, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fact_id, f"mem-{fact_id}", "default", "personal",
                 f"content {fact_id}", "semantic", "[]", "[]", 0.9, 0.5, 1, 0, 0,
                 "[]", "s1", 0, "active", 0, 0.0, 0.0, "factual", _NOW),
            )
        for index in range(size):
            db.execute(
                "INSERT INTO graph_edges (edge_id, profile_id, source_id, "
                "target_id, edge_type, weight, created_at) VALUES (?,?,?,?,?,?,?)",
                (f"e{index}", "default", f"f{index:02d}",
                 f"f{(index + 1) % size:02d}", "semantic", 1.0, _NOW),
            )


@pytest.fixture()
def spy(monkeypatch):
    """Records which engine ran, so "reported" can be checked against "used"."""
    import superlocalmemory.core.graph_metrics as graph_metrics

    ran: list[str] = []
    real_cozo = graph_metrics._cozo_metrics
    real_networkx = graph_metrics._networkx_metrics

    def watched_cozo(*args, **kwargs):
        ran.append("cozo")
        return real_cozo(*args, **kwargs)

    def watched_networkx(*args, **kwargs):
        ran.append("networkx")
        return real_networkx(*args, **kwargs)

    monkeypatch.setattr(graph_metrics, "_cozo_metrics", watched_cozo)
    monkeypatch.setattr(graph_metrics, "_networkx_metrics", watched_networkx)
    return ran


@pytest.fixture()
def projection(tmp_path, request):
    if _pycozo is None:
        pytest.skip("the graph projection needs its engine installed")
    from superlocalmemory.graph.cozo_backend import CozoDBGraphBackend

    return CozoDBGraphBackend(str(tmp_path / "graph.cozo"))


def _import(projection, config, db):
    source = sqlite3.connect(str(config.db_path))
    source.row_factory = sqlite3.Row
    projection.bulk_import_from_sqlite(source, "default")
    source.close()
    db.execute("DELETE FROM projection_outbox")


def test_asking_for_the_in_process_engine_runs_the_in_process_engine(
    tmp_path, monkeypatch, spy, projection,
):
    """Having a projection available is not a reason to use it."""
    from superlocalmemory.core.graph_metrics import compute_graph_metrics

    db, config = _store(tmp_path, monkeypatch)
    _ring(db)
    _import(projection, config, db)

    report = compute_graph_metrics(
        db, "default", backend=projection, prefer="networkx",
    )
    assert report.engine == "networkx"
    assert spy == ["networkx"], f"the report said networkx and {spy} ran"


def test_asking_for_the_graph_engine_runs_the_graph_engine(
    tmp_path, monkeypatch, spy, projection,
):
    from superlocalmemory.core.graph_metrics import compute_graph_metrics

    db, config = _store(tmp_path, monkeypatch)
    _ring(db)
    _import(projection, config, db)

    report = compute_graph_metrics(db, "default", backend=projection, prefer="cozo")
    assert report.engine == "cozo"
    assert spy == ["cozo"]


def test_both_engines_produce_the_same_ranking_on_the_same_graph(
    tmp_path, monkeypatch, projection,
):
    """Interchangeable means interchangeable. A cycle is symmetric, so every
    fact must come out with the same share, and the shares must total one."""
    from superlocalmemory.core.graph_metrics import compute_graph_metrics

    db, config = _store(tmp_path, monkeypatch)
    _ring(db)
    _import(projection, config, db)

    def scores():
        return {
            dict(row)["fact_id"]: dict(row)["pagerank_score"]
            for row in db.execute(
                "SELECT fact_id, pagerank_score FROM fact_importance "
                "WHERE profile_id = 'default'"
            )
        }

    compute_graph_metrics(db, "default", backend=projection, prefer="networkx")
    in_process = scores()
    compute_graph_metrics(db, "default", backend=projection, prefer="cozo")
    projected = scores()

    assert set(in_process) == set(projected)
    assert abs(sum(in_process.values()) - 1.0) < 0.01, (
        "a ranking is a share of one whole graph; if the shares do not total "
        "one, the thresholds that read them mean nothing"
    )
    assert abs(sum(projected.values()) - 1.0) < 0.01
    worst = max(abs(in_process[key] - projected[key]) for key in in_process)
    assert worst < 0.01, f"the two engines disagree by {worst:.4f} on one graph"


def test_a_projection_with_unapplied_changes_is_not_ranked_from(
    tmp_path, monkeypatch, spy, projection,
):
    """A queued change means the projection is a graph the store no longer has."""
    from superlocalmemory.core.graph_metrics import compute_graph_metrics
    from superlocalmemory.storage import projection_outbox

    db, config = _store(tmp_path, monkeypatch)
    _ring(db)
    _import(projection, config, db)
    projection_outbox.enqueue(db, "f00", "default")

    report = compute_graph_metrics(db, "default", backend=projection, prefer="cozo")
    assert report.engine == "networkx"
    assert spy == ["networkx"]
    assert any("unapplied" in note for note in report.notes), (
        f"the reason the projection was declined is not in the report: {report.notes}"
    )


def test_with_no_projection_at_all_the_in_process_engine_answers(
    tmp_path, monkeypatch, spy,
):
    """The control: nothing about this depends on a projection existing."""
    from superlocalmemory.core.graph_metrics import compute_graph_metrics

    db, _config = _store(tmp_path, monkeypatch)
    _ring(db)
    report = compute_graph_metrics(db, "default", backend=None, prefer="cozo")
    assert report.engine == "networkx"
    assert spy == ["networkx"]
    assert report.facts == 12
