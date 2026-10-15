# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""``slm db repair-entities`` and its daemon route: preview by default, a change
only for the named data folder, a backup copy first, and an undo."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from superlocalmemory.cli import integrity_cmd
from superlocalmemory.server.routes import integrity
from tests.test_storage.test_entity_link_repair import _build, _state


@pytest.fixture()
def root(tmp_path: Path, monkeypatch) -> Path:
    _build(tmp_path / "memory.db")
    monkeypatch.setattr(integrity_cmd, "_root", lambda: tmp_path)
    monkeypatch.setattr("superlocalmemory.cli.daemon.is_daemon_running", lambda: False)
    return tmp_path


def _args(**kw) -> Namespace:
    return Namespace(**{"apply": False, "undo": "", "root": "", "json": True, **kw})


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_preview_changes_nothing(root, capsys) -> None:
    before = _state(root / "memory.db")
    assert integrity_cmd.cmd_db_repair_entities(_args()) == 0
    data = _out(capsys)["data"]
    assert data["facts_relinked"] == 6 and data["changed"] is False
    assert _state(root / "memory.db") == before


def test_a_change_needs_the_data_folder_named(root, tmp_path, capsys) -> None:
    before = _state(root / "memory.db")
    assert integrity_cmd.cmd_db_repair_entities(_args(apply=True, root="/elsewhere")) == 2
    assert _state(root / "memory.db") == before


def test_apply_backs_up_then_repairs_and_undo_restores(root, capsys) -> None:
    before = _state(root / "memory.db")
    assert integrity_cmd.cmd_db_repair_entities(_args(apply=True, root=str(root))) == 0
    data = _out(capsys)["data"]
    assert data["facts_relinked"] == 6 and data["backup"]
    assert list((root / "backups").glob("*pre-entity-repair*"))
    assert integrity_cmd.cmd_db_repair_entities(_args(undo=data["run_id"], root=str(root))) == 0
    assert _state(root / "memory.db") == before


def test_the_route_refuses_another_data_folder(root, monkeypatch) -> None:
    monkeypatch.setattr(integrity, "_manage", lambda request: None)
    monkeypatch.setattr(integrity, "_db_path", lambda request: root / "memory.db")
    monkeypatch.setattr("superlocalmemory.infra.data_root.canonical_data_root", lambda: root)
    app = FastAPI()
    app.include_router(integrity.router)
    client = TestClient(app)
    before = _state(root / "memory.db")
    assert client.post("/api/integrity/repair-entities",
                       json={"root": "/elsewhere"}).status_code == 409
    assert _state(root / "memory.db") == before
    body = client.post("/api/integrity/repair-entities", json={"root": str(root)}).json()
    assert body["facts_relinked"] == 6 and body["ran_in"] == "daemon"
    again = client.post("/api/integrity/repair-entities",
                        json={"root": str(root), "undo_run_id": "nope"})
    assert again.status_code == 409


def test_the_route_needs_the_write_credential(root) -> None:
    app = FastAPI()
    app.include_router(integrity.router)
    response = TestClient(app).post("/api/integrity/repair-entities", json={"root": str(root)})
    assert response.status_code in (401, 403)
