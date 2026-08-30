# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""A setting is an intention; the dashboard was reporting it as a fact.

Promoting to a bigger backend writes the choice into the configuration. A
promotion that never finished leaves the setting saying ``cozo`` while every
query is still answered by SQLite — and the settings screen read the setting,
so it agreed with the mistake and the operator had no way to notice.

On a real installation: ``graph_backend: cozo``, ``vector_backend: lancedb``,
``scale_engine_state: verified``, and neither directory on disk.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.server.routes import config_api


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config_api, "MEMORY_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "graph_backend": "cozo",
        "vector_backend": "lancedb",
        "scale_engine_state": "verified",
    }), encoding="utf-8")
    app = FastAPI()
    app.include_router(config_api.router)
    return TestClient(app)


def test_a_promotion_that_never_happened_is_visible(client) -> None:
    body = client.get("/api/v3/storage/config").json()

    assert body["graph_backend"] == "cozo", "the setting is still reported as set"
    assert body["graph_backend_active"] == "sqlite", (
        "the dashboard claims a backend whose data directory does not exist"
    )
    assert body["vector_backend_active"] == "sqlite-vec"
    assert body["backend_matches_configuration"] is False


def test_a_matching_configuration_says_so(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config_api, "MEMORY_DIR", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "graph_backend": "sqlite",
        "vector_backend": "sqlite-vec",
    }), encoding="utf-8")
    app = FastAPI()
    app.include_router(config_api.router)

    body = TestClient(app).get("/api/v3/storage/config").json()

    assert body["backend_matches_configuration"] is True
    assert body["graph_backend_active"] == "sqlite"


def test_both_the_library_and_its_data_are_required(monkeypatch, tmp_path) -> None:
    """Neither alone is enough, and the library must IMPORT, not merely exist."""
    monkeypatch.setattr(config_api, "MEMORY_DIR", tmp_path)
    import importlib

    real = importlib.import_module

    def importable(name, *args, **kwargs):
        if name in ("pycozo", "lancedb"):
            return object()
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", importable)

    # The libraries import, but no data directory exists.
    assert config_api._active_backends("cozo", "lancedb") == ("sqlite", "sqlite-vec")

    # Both true.
    (tmp_path / "cozo").mkdir()
    assert config_api._active_backends("cozo", "lancedb")[0] == "cozo"


def test_a_library_present_but_broken_is_not_active(monkeypatch, tmp_path) -> None:
    """A native extension that no longer matches the interpreter is on disk
    and raises on import. Finding the file would have called it active."""
    monkeypatch.setattr(config_api, "MEMORY_DIR", tmp_path)
    (tmp_path / "cozo").mkdir()
    import importlib

    real = importlib.import_module

    def broken(name, *args, **kwargs):
        if name == "pycozo":
            raise ImportError("dlopen failed: incompatible architecture")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", broken)

    assert config_api._active_backends("cozo", "lancedb")[0] == "sqlite", (
        "a backend whose library will not import was reported as serving queries"
    )
