"""Timeout answers must admit sources with the same rules as full recall."""

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from superlocalmemory.retrieval import source_fidelity_flags
from superlocalmemory.server.recall_fallback import recall_keyword_fallback
from superlocalmemory.storage.models import RecallResponse
from tests.test_core.test_wp5b_fidelity_failure import DAMAGED, SOURCE, _candidates


@pytest.mark.parametrize("state", [
    "healthy", "lineage", "checker", "sources", "released", "orphan", "empty", "wrong_owner",
])
@pytest.mark.parametrize("surface", ["recall", "dashboard"])
def test_timeout_never_returns_damaged_unchecked_derivatives(
    tmp_path, engine_with_mock_deps, monkeypatch, state, surface,
):
    db, candidates = _candidates(tmp_path)
    engine = engine_with_mock_deps
    monkeypatch.setattr(engine, "_db", db)
    engine.profile_id = "default"
    if state == "lineage":
        db.execute("DROP TABLE derivation_lineage")
    elif state == "sources":
        with db.raw_connection() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE memories")
    elif state == "checker":
        def broken(*a, **k):
            raise ValueError("private source must never be logged")
        monkeypatch.setattr(source_fidelity_flags, "check_fact_against_source", broken)
    elif state == "empty":
        db.execute("UPDATE memories SET content=''")
    elif state == "wrong_owner":
        db.execute("INSERT INTO profiles (profile_id,name) VALUES ('peer','peer')")
        db.execute("UPDATE memories SET profile_id='peer'")
    elif state == "orphan":
        with db.raw_connection() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM memories")
    elif state == "released":
        from superlocalmemory.core.source_fidelity_guard import RELEASED_PREFIX
        db.execute(
            "INSERT INTO derivation_lineage "
            "(lineage_id,profile_id,object_type,object_id,unresolved_reason,source_status) "
            "VALUES (?,?,?,?,?,'exact')",
            ("release", "default", "fact", candidates[0].fact.fact_id, RELEASED_PREFIX + "user"),
        )

    def slow(*a, **k):
        time.sleep(0.15)
        return RecallResponse()
    monkeypatch.setattr(engine, "recall", slow)
    monkeypatch.setenv("SLM_SEARCH_RECALL_TIMEOUT_S", "0.01")
    # Unreadable sources are deliberately not repaired by the writer fixture.
    if surface == "recall":
        from superlocalmemory.server.unified_daemon import create_app
        app = create_app()
        app.state.engine = engine
        response = TestClient(app).get(
            "/recall", params={"q": "publish approval", "limit": 10,
                            "answer_check": "skip"})  # the hook path keeps the fallback
    else:
        from superlocalmemory.server.routes import memories
        from superlocalmemory.storage.read_connection import ReadConnectionFactory
        monkeypatch.setattr(
            memories, "get_db_connection", lambda: ReadConnectionFactory(db.db_path).open())
        monkeypatch.setattr(memories, "get_active_profile", lambda: "default")
        from superlocalmemory.server.unified_daemon import create_app
        app = create_app()
        app.state.engine = engine
        response = TestClient(app).post(
            "/api/search", json={"query": "publish approval", "limit": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["retrieval_mode"] == "degraded_lexical"
    expected = [] if state in {"sources", "orphan", "empty", "wrong_owner"} else [SOURCE]
    if state == "released":
        expected.append(DAMAGED)
    assert sorted(r["content"] for r in body["results"]) == sorted(expected)
    assert body["source_fidelity_status"] == (
        "degraded" if state in {
            "lineage", "checker", "sources", "orphan", "empty", "wrong_owner"} else "checked")
    assert bool(body["source_fidelity_note"]) == (state not in {"healthy", "released"})
    db.close()


def test_stalled_fidelity_check_exhausts_only_fallback_budget(tmp_path, monkeypatch):
    db, _ = _candidates(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    original = source_fidelity_flags.check_fact_against_source
    def stalled(*a, **k):
        entered.set()
        assert finish.wait(5)
        return original(*a, **k)
    monkeypatch.setattr(source_fidelity_flags, "check_fact_against_source", stalled)
    try:
        start = time.monotonic()
        body = recall_keyword_fallback(
            SimpleNamespace(_db=db, profile_id="default"), "publish", 10,
            fidelity_budget_s=0.02,
        )
        assert time.monotonic() - start < 0.5
        assert entered.is_set()
        assert body["results"] == []
        assert body["source_fidelity_status"] == "degraded"
        assert "unchecked derivatives withheld" in body["source_fidelity_note"]
        # More stalled requests must not create an unbounded set of workers.
        engine = SimpleNamespace(_db=db, profile_id="default")
        for _ in range(5):
            assert recall_keyword_fallback(
                engine, "publish", 10, fidelity_budget_s=0.02)["results"] == []
        assert sum(t.name == "slm-fallback-fidelity" and t.is_alive()
                   for t in threading.enumerate()) <= 2
    finally:
        finish.set()
        from superlocalmemory.retrieval.fallback_fidelity import join_fallback_checks
        assert join_fallback_checks(5)
        db.close()


def test_worker_start_refusal_releases_capacity_and_read_lease(tmp_path, monkeypatch):
    from superlocalmemory.retrieval.fallback_fidelity import admit_fallback, join_fallback_checks
    from superlocalmemory.storage.read_connection import ReadConnectionFactory
    db, _ = _candidates(tmp_path)
    rows = [dict(r) for r in db.execute(
        "SELECT fact_id,memory_id,profile_id,content FROM atomic_facts")]
    lease = ReadConnectionFactory(db.db_path).open()
    with monkeypatch.context() as patch:
        def refuse(*a, **k):
            raise RuntimeError("no threads available")
        patch.setattr(threading.Thread, "start", refuse)
        out, response = admit_fallback(rows, lease, "default", close_db=True)
        assert out == [] and response.source_fidelity_status == "degraded"
        assert lease._closed
        assert join_fallback_checks()
    assert [r["content"] for r in admit_fallback(rows, db, "default")[0]] == [SOURCE]
    assert join_fallback_checks()
    db.close()
