"""The adapter setup route can only point an importer at folders the owner chose.

PR 251 review, finding 5: /api/adapters/configure accepted any absolute path
the daemon's user could read (a folder for transcripts, any file as MBOX or
ICS), behind workspace MANAGE. In company mode a signed-in workspace admin,
possibly on another machine, could make the daemon ingest arbitrary local
files and then read them back as memories.

Now: installation-wide MANAGE, and the path must resolve inside an import
folder (``<data root>/imports``, or a folder the owner approved with
``slm adapters allow-folder``), never through a symlink out of it, never into
a hidden folder, never into the data root itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from superlocalmemory.ingestion import adapter_manager as manager


@pytest.fixture(autouse=True)
def _no_leftover_workers(monkeypatch):
    """Each test starts with no remembered worker (a fake one would leak into the next)."""
    monkeypatch.setattr(manager, "_CHILDREN", {})


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SLM_DATA_DIR", str(data))
    monkeypatch.setattr(manager, "_SLM_HOME", data)
    monkeypatch.setattr(manager, "_ADAPTERS_CONFIG", data / "adapters.json")
    return data


@pytest.fixture
def client(root, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from superlocalmemory.server import rbac_enforce
    from superlocalmemory.server.routes.adapters import router

    seen = []
    monkeypatch.setattr(rbac_enforce, "require_global_manage", lambda request: seen.append(1))
    app = FastAPI()
    app.include_router(router)
    test_client = TestClient(app)
    test_client.gate_calls = seen
    return test_client


def configure(client, name, path):
    return client.post("/api/adapters/configure", json={"name": name, "path": str(path)})


def test_configure_requires_installation_wide_manage(client, root):
    folder = root / "imports" / "calls"
    folder.mkdir(parents=True)
    assert configure(client, "transcript", folder).status_code == 200
    assert client.gate_calls == [1]


def test_configure_refuses_a_workspace_admin(root, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from superlocalmemory.server import rbac_enforce
    from superlocalmemory.server.routes.adapters import router

    def refuse(request):
        raise HTTPException(403, "installation-wide")

    monkeypatch.setattr(rbac_enforce, "require_global_manage", refuse)
    monkeypatch.setattr(rbac_enforce, "require_manage", lambda request: None)
    app = FastAPI()
    app.include_router(router)
    folder = root / "imports"
    folder.mkdir()
    assert configure(TestClient(app), "transcript", folder).status_code == 403


def test_a_folder_inside_the_import_root_is_accepted(client, root):
    mbox = root / "imports" / "mail.mbox"
    mbox.parent.mkdir()
    mbox.write_text("From x\n")
    assert configure(client, "gmail", mbox).status_code == 200
    saved = json.loads((root / "adapters.json").read_text())
    assert saved["gmail"]["mbox_path"] == str(mbox.resolve())


@pytest.mark.parametrize("where", ["outside", "data_root", "hidden", "symlink_escape"])
def test_paths_outside_the_import_roots_are_refused(client, root, tmp_path, where):
    imports = root / "imports"
    imports.mkdir()
    secret = tmp_path / "outside" / "secret.ics"
    secret.parent.mkdir()
    secret.write_text("BEGIN:VCALENDAR\n")
    target = {
        "outside": secret,
        "data_root": root / "config.json",
        "hidden": imports / ".ssh" / "keys.ics",
        "symlink_escape": imports / "link.ics",
    }[where]
    if where == "data_root":
        target.write_text("{}")
    if where == "hidden":
        target.parent.mkdir()
        target.write_text("BEGIN:VCALENDAR\n")
    if where == "symlink_escape":
        target.symlink_to(secret)
    response = configure(client, "calendar", target)
    assert response.status_code == 422, response.text
    assert "import folder" in response.json()["detail"]
    assert not (root / "adapters.json").exists()


def test_an_owner_approved_folder_is_accepted(client, root, tmp_path, capsys):
    folder = tmp_path / "Meetings"
    folder.mkdir()
    assert configure(client, "transcript", folder).status_code == 422
    manager.handle_adapters_cli(["allow-folder", str(folder)])
    assert "approved" in capsys.readouterr().out.lower()
    assert configure(client, "transcript", folder).status_code == 200


def test_the_owner_cannot_approve_the_data_root_or_the_filesystem_root(root, capsys):
    from superlocalmemory.ingestion import import_roots

    for bad in (root, Path("/")):
        with pytest.raises(ValueError):
            import_roots.approve(bad, root)


def test_a_symlinked_transcript_inside_the_folder_is_not_read(tmp_path):
    from superlocalmemory.ingestion.transcript_adapter import TranscriptAdapter

    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "real.txt").write_text("a real transcript")
    outside = tmp_path / "private.txt"
    outside.write_text("not for import")
    (watched / "escape.txt").symlink_to(outside)
    adapter = TranscriptAdapter(watch_dir=watched)
    adapter._scan_existing()
    assert [p.name for p in adapter._pending_files] == ["real.txt"]
    assert adapter._admissible(SimpleNamespace(is_symlink=lambda: True)) is False
