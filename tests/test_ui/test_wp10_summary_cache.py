import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import summaries as routes
from superlocalmemory.summaries.base import SummaryResult


def test_daily_summary_cache_is_profile_and_write_sensitive(tmp_path, monkeypatch):
    import superlocalmemory.summaries as generators

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    profile = ["default"]
    monkeypatch.setattr(routes, "get_active_profile", lambda: profile[0])
    monkeypatch.setattr(routes, "_load_config", lambda: None)
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("CREATE TABLE change(value TEXT)")
    calls = []

    def generate(*args, **kwargs):
        calls.append(args[2])
        return SummaryResult(
            kind="daily",
            profile_id=args[2],
            content="summary",
            coverage="full",
            generated_by="extractive",
            source_fact_ids=[],
        )

    monkeypatch.setattr(generators, "generate_daily_reflection", generate)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    params = {"kind": "day", "target": "2026-10-11"}
    assert client.get("/api/summary", params=params).status_code == 200
    client.get("/api/summary", params=params)
    assert len(calls) == 1
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("INSERT INTO change VALUES('changed')")
    client.get("/api/summary", params=params)
    assert len(calls) == 2
    profile[0] = "other"
    client.get("/api/summary", params=params)
    assert calls == ["default", "default", "other"]
