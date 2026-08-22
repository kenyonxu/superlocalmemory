# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Choosing a model should be a choice from a list, checked before it is saved.

Typing a model name into a settings field and pressing save is how a user ends
up with a store that cannot answer questions about itself: the name was wrong,
or it was right and emitted vectors of a different width. Both are silent. These
routes make the check happen while the user is still looking at the screen.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import config_api


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config_api, "MEMORY_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "embedding": {"provider": "ollama", "ollama_model": "nomic-embed-text",
                      "model_name": "nomic-ai/nomic-embed-text-v1.5", "dimension": 768},
        "llm": {"provider": "ollama", "model": "llama3.2",
                "base_url": "http://localhost:11434"},
    }), encoding="utf-8")
    monkeypatch.setattr(config_api, "_require_admin", lambda request: None)

    app = FastAPI()
    app.include_router(config_api.router)
    return TestClient(app)


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_the_list_of_models_says_which_two_are_in_use(client, monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(200, {
        "models": [{"name": "llama3.2:latest", "size": 2019393189},
                   {"name": "nomic-embed-text:latest", "size": 274302450}],
    }))
    body = client.get("/api/v3/ollama/models").json()
    assert body["reachable"] is True
    assert [m["name"] for m in body["installed"]] == [
        "llama3.2:latest", "nomic-embed-text:latest",
    ]
    assert body["embedding_model"] == "nomic-embed-text"
    assert body["generation_model"] == "llama3.2"


def test_a_stopped_server_is_reported_not_hidden(client, monkeypatch) -> None:
    def refuse(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", refuse)

    body = client.get("/api/v3/ollama/models").json()
    assert body["reachable"] is False
    assert "ollama serve" in body["detail"]
    assert body["installed"] == []


def test_a_model_of_a_different_width_is_refused_before_saving(
    client, monkeypatch, tmp_path
) -> None:
    import sqlite3

    conn = sqlite3.connect(tmp_path / "memory.db")
    conn.execute("CREATE TABLE embedding_metadata (vec_rowid INTEGER PRIMARY KEY,"
                 " fact_id TEXT, model_name TEXT, dimension INTEGER)")
    conn.execute("INSERT INTO embedding_metadata VALUES (1,'f','nomic',768)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(
        200, {"embeddings": [[0.0] * 1024]}))

    body = client.post("/api/v3/ollama/validate", json={
        "model_name": "mxbai-embed-large", "role": "embedding",
    }).json()
    assert body["safe_to_apply"] is False
    assert body["stored_dimension"] == 768
    assert body["dimension"] == 1024
    assert "cannot be compared" in body["message"]


def test_a_generation_model_is_checked_for_the_generation_role(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(
        200, {"response": "ok"}))
    body = client.post("/api/v3/ollama/validate", json={
        "model_name": "llama3.2", "role": "generation",
    }).json()
    assert body["ok"] is True
    assert body["dimension"] is None


def test_an_unknown_role_is_rejected_by_the_schema(client) -> None:
    response = client.post("/api/v3/ollama/validate", json={
        "model_name": "llama3.2", "role": "summarising",
    })
    assert response.status_code == 422


def test_an_empty_model_name_is_rejected_by_the_schema(client) -> None:
    response = client.post("/api/v3/ollama/validate", json={
        "model_name": "", "role": "embedding",
    })
    assert response.status_code == 422
