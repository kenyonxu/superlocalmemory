# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""BRN-21: the dashboard separates liveness from learning evidence."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_UI = Path(__file__).resolve().parents[2] / "src" / "superlocalmemory" / "ui"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    from superlocalmemory.server import rbac_enforce
    from superlocalmemory.server.routes import learning_readiness

    monkeypatch.setattr(learning_readiness, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(learning_readiness, "get_active_profile", lambda: "default")
    monkeypatch.setattr(rbac_enforce, "require_permission", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(learning_readiness.router)
    return TestClient(app)


def test_a_live_daemon_with_no_evidence_reports_not_demonstrated(
        tmp_path: Path, monkeypatch) -> None:
    from tests.test_storage._upgrade_store import current_store

    current_store(tmp_path)
    body = _client(tmp_path, monkeypatch).get("/api/learning/readiness").json()
    assert body["available"] is True
    assert body["learning_demonstrated"] is False
    assert body["status"] == "collecting"
    assert body["active_model"] is None and body["settled_from_outcomes"] == 0


def test_the_health_page_no_longer_equates_green_with_learning() -> None:
    js = (_UI / "js" / "od-health.js").read_text(encoding="utf-8")
    assert "learning correctly" not in js
    assert "'port 8765" not in js
    assert "/api/learning/readiness" in js
    assert "Not demonstrated" in js
