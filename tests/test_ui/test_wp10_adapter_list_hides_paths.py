"""The adapter list shows configured paths only to installation-wide managers.

PR 251 round 2: GET /api/adapters answered anyone and included each
adapter's configured folder, so any reader (in company mode, any member of
any workspace) learned local filesystem paths. Readers still get the list
the Ingestion cards need; the paths are left out unless the caller holds
installation-wide MANAGE.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from superlocalmemory.ingestion import adapter_manager as manager


@pytest.fixture(autouse=True)
def _no_leftover_workers(monkeypatch):
    """Each test starts with no remembered worker (a fake one would leak into the next)."""
    monkeypatch.setattr(manager, "_CHILDREN", {})


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "imports" / "calls").mkdir(parents=True)
    monkeypatch.setenv("SLM_DATA_DIR", str(data))
    monkeypatch.setattr(manager, "_SLM_HOME", data)
    monkeypatch.setattr(manager, "_ADAPTERS_CONFIG", data / "adapters.json")
    manager._save_config({"transcript": {"enabled": True, "tier": "auto",
                                         "watch_dir": str(data / "imports" / "calls")}})
    return data


def client_as(monkeypatch, *, reader: bool, manager_role: bool):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server import rbac_enforce
    from superlocalmemory.server.routes.adapters import router

    def require_permission(request, permission, **kwargs):
        if not reader:
            raise HTTPException(401, "Sign in to continue.")

    monkeypatch.setattr(rbac_enforce, "require_permission", require_permission)
    monkeypatch.setattr(rbac_enforce, "has_global_manage", lambda request: manager_role)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def transcript(response):
    return next(a for a in response.json()["adapters"] if a["name"] == "transcript")


def test_an_installation_manager_sees_the_configured_folder(root, monkeypatch):
    response = client_as(monkeypatch, reader=True, manager_role=True).get("/api/adapters")
    assert response.status_code == 200
    assert transcript(response)["watch_dir"] == str(root / "imports" / "calls")
    assert response.json()["paths_hidden"] is False


def test_a_reader_gets_the_cards_without_any_path(root, monkeypatch):
    response = client_as(monkeypatch, reader=True, manager_role=False).get("/api/adapters")
    assert response.status_code == 200
    card = transcript(response)
    assert card["enabled"] is True and card["state"] in ("stopped", "running", "failed")
    assert card["watch_dir"] == ""
    assert str(root) not in response.text
    assert response.json()["paths_hidden"] is True


def test_a_caller_without_read_is_refused(root, monkeypatch):
    response = client_as(monkeypatch, reader=False, manager_role=False).get("/api/adapters")
    assert response.status_code == 401
