import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import summaries
from superlocalmemory.storage.schema import create_all_tables


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        create_all_tables(conn)
        conn.execute("INSERT INTO profiles(profile_id,name) VALUES('other','Other')")
        for i in range(80):
            conn.execute(
                "INSERT INTO memory_scenes(scene_id,profile_id,theme,fact_ids_json) "
                "VALUES(?,?,?,?)",
                (f"c{i:03}", "default", "Topic", json.dumps([str(i)] * (i + 1))),
            )
            conn.execute(
                "INSERT INTO trust_scores(target_id,target_type,profile_id,"
                "trust_score,evidence_count) "
                "VALUES(?,?,?,?,?)",
                (f"t{i:03}", "agent", "default", i / 100, i),
            )
        conn.execute(
            "INSERT INTO memory_scenes(scene_id,profile_id,theme,fact_ids_json) "
            "VALUES('hidden','other','hidden','[\"x\"]')"
        )
    app = FastAPI()
    app.include_router(summaries.router)
    return TestClient(app)


def test_cluster_feed_pages_before_serializing_and_sorts_globally(client):
    one = client.get("/api/dashboard/clusters", params={"limit": 10, "offset": 0}).json()
    two = client.get("/api/dashboard/clusters", params={"limit": 10, "offset": 10}).json()
    assert one["total_clusters"] == 80
    assert len(one["clusters"]) == len(two["clusters"]) == 10
    assert one["clusters"][0]["cluster_id"] == "c079"
    assert two["clusters"][0]["cluster_id"] == "c069"
    assert "hidden" not in json.dumps(one)
    assert len(json.dumps(one)) < 10000


def test_trust_feed_pages_and_aggregate_counts_cover_all_rows(client):
    data = client.get("/api/dashboard/trust", params={"limit": 10, "offset": 10}).json()
    assert data["total"] == 80
    assert len(data["agents"]) == 10
    assert data["agents"][0]["target_id"] == "t069"
    assert data["alert_count"] == 30
    assert data["avg_trust"] == pytest.approx(0.395)


@pytest.mark.parametrize("path", ["clusters", "trust"])
def test_feeds_validate_bounds_and_sort_before_sql(client, path):
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"sort": "bad;DROP TABLE"}):
        assert client.get("/api/dashboard/" + path, params=params).status_code == 422
