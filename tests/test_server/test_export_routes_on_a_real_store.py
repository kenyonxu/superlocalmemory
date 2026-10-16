# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``/api/export`` and ``/api/compliance/gdpr/export`` on a store the engine wrote.

4.1.25 audit G-C2: both routes answered 500 / "Internal server error" on a real
store because an embedding is a BLOB, and the dashboard said "Export started"
anyway. These tests export a store holding every awkward column type, over HTTP,
and check both the file and what the caller is told when an export fails.
"""

from __future__ import annotations

import base64
import csv
import gzip
import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from superlocalmemory.server.profile_runtime import bind_profile_runtime
from superlocalmemory.server.routes import compliance, data_io
from superlocalmemory.server.unified_daemon import create_app
from tests.test_compliance.awkward_store import (
    EMBEDDING_BYTES,
    PROFILE,
    build_awkward_store,
)


@pytest.fixture()
def client(engine_with_mock_deps):
    engine = engine_with_mock_deps
    build_awkward_store(engine)
    engine.profile_id = PROFILE
    engine._config.active_profile = PROFILE
    app = create_app()
    app.state.engine = engine
    app.state.config = engine._config
    bind_profile_runtime(app.state, engine, engine._config)

    import sqlite3

    def connect():
        return sqlite3.connect(f"file:{engine._db.db_path}?mode=ro", uri=True)

    with patch.object(data_io, "get_db_connection", connect), \
            patch.object(data_io, "get_active_profile", lambda: PROFILE), \
            patch.object(compliance, "get_active_profile", lambda: PROFILE):
        yield TestClient(app), engine


def _body(response) -> bytes:
    data = response.content
    return gzip.decompress(data) if response.headers["content-type"] == "application/gzip" else data


def test_api_export_json_succeeds_on_a_store_with_blob_embeddings(client) -> None:
    http, _ = client
    reply = http.get("/api/export?format=json")
    assert reply.status_code == 200, reply.text
    doc = json.loads(_body(reply))
    facts = {m["fact_id"]: m for m in doc["memories"]}
    assert set(facts) >= {"f-embedded", "f-hindi", "f-plain", "f-badutf8"}
    assert "embedding" not in facts["f-embedded"] and "fisher_mean" not in facts["f-embedded"]
    assert doc["total_memories"] == len(doc["memories"])
    assert doc["embeddings"] == "omitted"
    assert reply.headers["x-slm-records"] == str(len(doc["memories"]))
    assert "attachment" in reply.headers["content-disposition"]


def test_api_export_can_include_embeddings_as_base64(client) -> None:
    http, _ = client
    reply = http.get("/api/export?format=json&include_embeddings=true")
    assert reply.status_code == 200, reply.text
    facts = {m["fact_id"]: m for m in json.loads(_body(reply))["memories"]}
    assert base64.b64decode(facts["f-embedded"]["embedding"]) == EMBEDDING_BYTES


def test_api_export_jsonl_has_one_valid_object_per_line(client) -> None:
    http, _ = client
    reply = http.get("/api/export?format=jsonl")
    assert reply.status_code == 200, reply.text
    lines = _body(reply).decode("utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert {p["fact_id"] for p in parsed} >= {"f-embedded", "f-plain"}


def test_api_export_csv_has_a_stable_header_and_no_binary(client) -> None:
    http, _ = client
    reply = http.get("/api/export?format=csv")
    assert reply.status_code == 200, reply.text
    rows = list(csv.DictReader(io.StringIO(_body(reply).decode("utf-8"))))
    assert rows and "embedding" not in rows[0] and "fact_id" in rows[0]
    assert {r["fact_id"] for r in rows} >= {"f-embedded", "f-hindi"}


def test_api_export_is_gzipped_once_it_is_big(client) -> None:
    http, engine = client
    with engine._db.raw_connection() as conn:
        conn.executemany(
            "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content) "
            "VALUES (?, 'm-plain', ?, ?)",
            [(f"g-{i}", PROFILE, f"a reasonably long sentence number {i} " * 3)
             for i in range(300)])
    reply = http.get("/api/export?format=json")
    assert reply.headers["content-type"] == "application/gzip"
    assert reply.headers["content-disposition"].endswith('.json.gz"') \
        or ".json.gz" in reply.headers["content-disposition"]
    assert len(json.loads(gzip.decompress(reply.content))["memories"]) >= 300


def test_api_export_failure_is_an_error_status_not_a_file(client) -> None:
    http, _ = client

    def boom(*_a, **_k):
        raise RuntimeError("disk exploded")

    with patch.object(data_io, "write_memories_export", boom):
        reply = http.get("/api/export?format=json")
    assert reply.status_code == 500
    assert "Export failed" in reply.json()["detail"]
    assert "disk exploded" not in reply.text  # the cause stays in the daemon log


def test_gdpr_export_succeeds_on_a_store_with_blob_embeddings(client) -> None:
    http, _ = client
    reply = http.get("/api/compliance/gdpr/export")
    assert reply.status_code == 200, reply.text
    assert reply.headers["content-type"].startswith("application/json")
    assert "attachment" in reply.headers["content-disposition"]
    doc = reply.json()
    assert doc["profile_id"] == PROFILE and "available" not in doc
    assert {f["fact_id"] for f in doc["facts"]} >= {"f-embedded", "f-plain"}
    assert doc["media"]["pictures"] and doc["audit_chain"]
    assert reply.headers["x-slm-records"] == str(doc["total_items"])


def test_gdpr_export_can_include_embeddings(client) -> None:
    http, _ = client
    doc = http.get("/api/compliance/gdpr/export?include_embeddings=true").json()
    facts = {f["fact_id"]: f for f in doc["atomic_facts"]}
    assert base64.b64decode(facts["f-embedded"]["embedding"]) == EMBEDDING_BYTES


def test_gdpr_export_failure_is_an_error_status_not_a_file(client) -> None:
    http, _ = client
    with patch("superlocalmemory.compliance.gdpr.GDPRCompliance.write_profile_export",
               side_effect=RuntimeError("disk exploded")):
        reply = http.get("/api/compliance/gdpr/export")
    assert reply.status_code == 500
    body = reply.json()
    assert "Export failed" in body["detail"] and "disk exploded" not in reply.text
    assert "content-disposition" not in reply.headers


def test_gdpr_export_with_no_engine_is_an_error_status(client) -> None:
    http, _ = client
    http.app.state.engine = None
    with patch.object(compliance, "get_engine_lazy", lambda _state: None):
        reply = http.get("/api/compliance/gdpr/export")
    assert reply.status_code == 503
    assert "Engine not initialized" in reply.json()["detail"]


def test_no_temp_file_is_left_behind(client, tmp_path, monkeypatch) -> None:
    import tempfile

    http, _ = client
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    http.get("/api/export?format=json")
    http.get("/api/compliance/gdpr/export")
    with patch("superlocalmemory.compliance.gdpr.GDPRCompliance.write_profile_export",
               side_effect=RuntimeError("x")):
        http.get("/api/compliance/gdpr/export")
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("slm-export")] == []


def test_withheld_summaries_stay_out_of_the_export(client) -> None:
    """The quarantine filter once never applied: its column probe read a dict row by index."""
    http, engine = client
    engine._db.execute("UPDATE atomic_facts SET quarantined = 1 WHERE fact_id = 'f-plain'")
    doc = json.loads(_body(http.get("/api/export?format=json")))
    facts = {m["fact_id"] for m in doc["memories"]}
    assert "f-plain" not in facts and "f-embedded" in facts


def test_export_filters_still_apply(client) -> None:
    http, engine = client
    engine._db.execute("UPDATE atomic_facts SET fact_type = 'opinion' WHERE fact_id = 'f-hindi'")
    doc = json.loads(_body(http.get("/api/export?format=json&category=opinion")))
    assert [m["fact_id"] for m in doc["memories"]] == ["f-hindi"]
    assert doc["filters"]["category"] == "opinion"
