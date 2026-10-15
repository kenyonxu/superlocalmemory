"""Export downloads an existing recovery point; it never spends retention.

PR 251 review, finding 7: every Export click created a new recovery point, and
creating one prunes the oldest beyond ``max_backups`` (10 by default), so ten
clicks evicted every older point, including the only one from before an
incident. Export failures surfaced as a raw 500.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

STORES = ("memory.db", "learning.db", "audit_chain.db", "pending.db", "media.db")


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server.routes import backup_recovery

    for name in STORES:
        with sqlite3.connect(tmp_path / name) as conn:
            conn.execute("CREATE TABLE marker(value TEXT)")
    (tmp_path / "backup_config.json").write_text(json.dumps({"max_backups": 2}))
    monkeypatch.setattr(backup_recovery, "_gate", lambda request: None)
    monkeypatch.setattr(backup_recovery, "_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(backup_recovery.router)
    return TestClient(app, raise_server_exceptions=False)


def ids(root):
    from superlocalmemory.server.recovery_inventory import points

    return [record["id"] for record in points(root)]


def test_repeated_exports_create_no_points_and_evict_nothing(api, tmp_path):
    from superlocalmemory.server import backup_bundle

    first = backup_bundle.create_point(tmp_path)["id"]
    second = backup_bundle.create_point(tmp_path)["id"]
    for _ in range(5):
        response = api.post("/api/backup/recovery/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
    assert sorted(ids(tmp_path)) == sorted([first, second])
    assert second in response.headers["content-disposition"]


def test_a_named_point_is_exported(api, tmp_path):
    from superlocalmemory.server import backup_bundle

    first = backup_bundle.create_point(tmp_path)["id"]
    backup_bundle.create_point(tmp_path)
    response = api.post("/api/backup/recovery/export", json={"id": first})
    assert response.status_code == 200
    assert first in response.headers["content-disposition"]


def test_with_no_point_yet_one_is_created_and_nothing_is_evicted(api, tmp_path):
    response = api.post("/api/backup/recovery/export")
    assert response.status_code == 200
    assert len(ids(tmp_path)) == 1


def test_a_damaged_point_is_explained_not_a_500(api, tmp_path):
    from superlocalmemory.server import backup_bundle

    point = backup_bundle.create_point(tmp_path)["id"]
    (tmp_path / "backups" / f"backup_{point}" / "memory.db").write_bytes(b"damaged")
    response = api.post("/api/backup/recovery/export")
    assert response.status_code == 409
    assert "verification failed" in response.json()["detail"].lower()


def test_an_unexpected_failure_is_explained_not_a_500(api, tmp_path, monkeypatch):
    from superlocalmemory.server import backup_bundle

    backup_bundle.create_point(tmp_path)

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(backup_bundle, "export_point", broken)
    response = api.post("/api/backup/recovery/export")
    assert response.status_code == 503
    assert "could not be prepared" in response.json()["detail"]
