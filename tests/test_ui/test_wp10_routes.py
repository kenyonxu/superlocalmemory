"""Dashboard tasks must use real route contracts in an isolated data root."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.core.config import SLMConfig
from superlocalmemory.server.profile_runtime import reconfigure_daemon_engine
from superlocalmemory.server.routes import summaries
from superlocalmemory.storage.models import Mode
from superlocalmemory.storage.schema import create_all_tables


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(summaries, "get_active_profile", lambda: "default")
    monkeypatch.setattr(summaries, "_load_config", lambda: None)
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        create_all_tables(conn)
        conn.execute("INSERT OR IGNORE INTO profiles(profile_id,name) VALUES('other','Other')")
        conn.execute(
            "INSERT INTO memories(memory_id,profile_id,content) VALUES('m','default','notes')"
        )
        for fid, profile, created in [
            ("a", "default", "2026-09-30T20:00:00+00:00"),
            ("b", "default", "2026-10-05T20:00:00+00:00"),
            ("hidden", "other", "2026-10-05T20:00:00+00:00"),
        ]:
            conn.execute(
                "INSERT INTO atomic_facts(fact_id,memory_id,profile_id,content,created_at) "
                "VALUES(?,'m',?,?,?)",
                (fid, profile, "note " + fid, created),
            )
    app = FastAPI()
    app.include_router(summaries.router)
    app.state.engine = SimpleNamespace(_embedder=SimpleNamespace(embed=lambda text: [0.1] * 768))
    return TestClient(app)


def test_week_crosses_month_and_uses_caller_timezone(client):
    r = client.get(
        "/api/summary/range",
        params={"start": "2026-10-01", "end": "2026-10-06", "tz_offset_minutes": 330},
    )
    assert r.status_code == 200
    assert r.json()["source_fact_ids"] == ["a", "b"]
    assert r.json()["metadata"]["date_basis"] == "stored_at"


@pytest.mark.parametrize(
    "start,end", [("bad", "2026-10-06"), ("2026-10-07", "2026-10-06"), ("2020-01-01", "2026-10-06")]
)
def test_date_range_rejects_invalid_or_unbounded_input(client, start, end):
    assert client.get("/api/summary/range", params={"start": start, "end": end}).status_code == 422


def test_empty_period_does_not_invent_activity(client):
    r = client.get("/api/summary/range", params={"start": "2025-10-01", "end": "2025-10-06"})
    assert r.status_code == 200
    assert r.json()["source_count"] == 0
    assert r.json()["coverage"] == "insufficient"


def test_local_embedding_test_uses_resident_worker(client, monkeypatch):
    from superlocalmemory.server import rbac_enforce

    monkeypatch.setattr(rbac_enforce, "require_manage", lambda request: None)
    r = client.post("/api/dashboard/embedding-test", json={})
    assert r.status_code == 200
    assert r.json()["dimension"] == 768
    client.app.state.engine._embedder = None
    assert client.post("/api/dashboard/embedding-test", json={}).status_code == 503


def test_mode_transition_persists_authoritative_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    cfg = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    cfg.save()
    SLMConfig.write_current_mode("a", tmp_path)
    cfg.mode = Mode.B
    reconfigure_daemon_engine(SimpleNamespace(), cfg, mode_change=True)
    assert (tmp_path / "current_mode").read_text().strip() == "b"
    assert (tmp_path / "mode_b.json").exists()
    assert SLMConfig.load(tmp_path / "config.json").mode == Mode.B


def test_failed_runtime_transition_does_not_publish_new_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    cfg = SLMConfig.for_mode(Mode.A, base_dir=tmp_path)
    cfg.save()
    SLMConfig.write_current_mode("a", tmp_path)
    cfg.mode = Mode.B

    def refuse(*args, **kwargs):
        raise RuntimeError("refused")

    with pytest.raises(RuntimeError, match="refused"):
        reconfigure_daemon_engine(SimpleNamespace(reconfigure_engine=refuse), cfg, mode_change=True)
    assert (tmp_path / "current_mode").read_text().strip() == "a"
