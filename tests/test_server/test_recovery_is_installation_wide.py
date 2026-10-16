"""Recovery acts on the whole installation, so it is an installation-wide action.

A recovery point holds every workspace's memories and the account tables
(users, memberships, sessions, the login policy). Reproduction from the PR 251
review: an administrator of ONE workspace could restore over the shared store,
turn company mode off by restoring a point taken before login was required,
and download every workspace's data with password and session hashes.

Now: every recovery route needs installation-wide MANAGE (the machine owner,
or an administrator of every workspace); a restore never brings back accounts,
sessions or the login policy from the point (the current ones carry forward);
and no export contains them.
"""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

LOOPBACK = ("127.0.0.1", 50123)


@pytest.fixture
def app_and_root(engine_with_mock_deps, monkeypatch):
    from superlocalmemory.access.rbac import RbacEngine
    from superlocalmemory.server import unified_daemon
    from superlocalmemory.server.profile_runtime import bind_profile_runtime
    from superlocalmemory.server.routes import backup as backup_routes
    from superlocalmemory.server.routes import backup_recovery
    from superlocalmemory.server.unified_daemon import create_app

    monkeypatch.setattr(unified_daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)
    engine = engine_with_mock_deps
    engine.profile_id = "default"
    engine._config.active_profile = "default"
    for p in ("default", "teamb"):
        engine._db.execute(
            "INSERT OR IGNORE INTO profiles (profile_id, name) VALUES (?, ?)", (p, p)
        )
    app = create_app()
    app.state.engine = engine
    app.state.config = engine._config
    app.state.rbac = RbacEngine(str(engine._config.db_path))
    bind_profile_runtime(app.state, engine, engine._config)
    db_path = Path(engine._config.db_path)
    monkeypatch.setattr(
        backup_routes, "_get_backup_manager",
        lambda: SimpleNamespace(db_path=db_path, backup_dir=db_path.parent / "backups",
                                list_backups=lambda: []),
    )
    spawned = []
    monkeypatch.setattr(backup_recovery, "_spawn", lambda root, pid: spawned.append(pid) or 0)
    return app, db_path.parent, spawned


def _company(rbac, alice_spaces):
    rbac.create_user("alice", "password-1234")
    rbac.create_user("bob", "password-5678")
    ids = {u["username"]: u["user_id"] for u in rbac.list_users()}
    for space in alice_spaces:
        rbac.set_membership(space, ids["alice"], "admin", added_by="test")
    rbac.set_membership("teamb", ids["bob"], "admin", added_by="test")
    rbac.set_require_login(True)
    return ids, rbac.create_session(ids["alice"])


def test_workspace_admin_is_refused_every_recovery_action(app_and_root):
    from superlocalmemory.server import backup_bundle

    app, root, spawned = app_and_root
    point = backup_bundle.create_point(root)
    _, alice = _company(app.state.rbac, ["default"])
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=LOOPBACK)
    headers = {"X-SLM-User-Session": alice}
    calls = [
        ("get", "/api/backup/recovery/list", None),
        ("get", "/api/backup/recovery/status", None),
        ("post", "/api/backup/recovery/create", None),
        ("post", "/api/backup/recovery/verify", {"id": point["id"]}),
        ("post", "/api/backup/recovery/export", None),
        ("post", "/api/backup/recovery/restore", {"id": point["id"], "confirm": "RESTORE"}),
        ("post", "/api/backup/export", None),
    ]
    for method, path, body in calls:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, (path, response.status_code, response.text)
    assert spawned == []


def test_an_administrator_of_every_workspace_may_restore(app_and_root):
    from superlocalmemory.server import backup_bundle

    app, root, spawned = app_and_root
    point = backup_bundle.create_point(root)
    _, alice = _company(app.state.rbac, ["default", "teamb"])
    client = TestClient(app, base_url="http://127.0.0.1:8765", client=LOOPBACK)
    response = client.post(
        "/api/backup/recovery/restore",
        json={"id": point["id"], "confirm": "RESTORE"},
        headers={"X-SLM-User-Session": alice},
    )
    assert response.status_code == 200, response.text
    assert spawned == [point["id"]]


def test_an_owner_restore_keeps_the_current_accounts_and_login_policy(app_and_root):
    from superlocalmemory.server import backup_bundle

    app, root, _ = app_and_root
    rbac = app.state.rbac
    # Point taken while the install was personal (no users, login optional).
    point = backup_bundle.create_point(root)
    ids, alice = _company(rbac, ["default"])

    backup_bundle.restore_point(root, point["id"])

    assert rbac.require_login() is True
    assert rbac.user_count() == 2
    session = rbac.resolve_session(alice)
    assert session is not None and session["user_id"] == ids["alice"]


def test_a_revoked_account_is_not_resurrected_by_a_restore(app_and_root):
    from superlocalmemory.server import backup_bundle

    app, root, _ = app_and_root
    rbac = app.state.rbac
    ids, alice = _company(rbac, ["default"])
    point = backup_bundle.create_point(root)
    with sqlite3.connect(root / "memory.db") as conn:
        conn.execute("DELETE FROM rbac_sessions")
        conn.execute("DELETE FROM rbac_memberships WHERE user_id = ?", (ids["alice"],))
    backup_bundle.restore_point(root, point["id"])
    assert rbac.resolve_session(alice) is None
    with sqlite3.connect(root / "memory.db") as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM rbac_memberships WHERE user_id = ?", (ids["alice"],)
        ).fetchone()[0]
    assert left == 0


def _hashes(root):
    with sqlite3.connect(root / "memory.db") as conn:
        passwords = [r[0] for r in conn.execute("SELECT password_hash FROM rbac_users")]
        tokens = [r[0] for r in conn.execute("SELECT token_hash FROM rbac_sessions")]
    return [h.encode() for h in passwords + tokens if h]


def test_a_recovery_export_holds_no_password_or_session_hashes(app_and_root, tmp_path):
    from superlocalmemory.server import backup_bundle

    app, root, _ = app_and_root
    _company(app.state.rbac, ["default"])
    secrets = _hashes(root)
    assert len(secrets) == 3
    point = backup_bundle.create_point(root)
    export = backup_bundle.export_point(root, point["id"])
    with zipfile.ZipFile(export) as archive:
        archive.extractall(tmp_path / "out")
        manifest = (tmp_path / "out" / "recovery.json").read_text()
    raw = (tmp_path / "out" / "memory.db").read_bytes()
    for secret in secrets:
        assert secret not in raw
    with sqlite3.connect(tmp_path / "out" / "memory.db") as conn:
        for table in ("rbac_users", "rbac_sessions", "rbac_memberships", "rbac_settings"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert "rbac_sessions" in manifest
    # The exported copy still verifies against its own manifest.
    import hashlib
    import json

    for entry in json.loads(manifest)["files"]:
        data = (tmp_path / "out" / entry["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["name"]


def test_the_full_archive_export_holds_no_password_or_session_hashes(app_and_root, tmp_path):
    import tarfile

    from superlocalmemory.infra.backup_archive import build_export

    app, root, _ = app_and_root
    _company(app.state.rbac, ["default"])
    secrets = _hashes(root)
    archive = tmp_path / "export.tar.gz"
    manifest = build_export(root, archive)
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("slm-backup/set/memory.db")
        raw = member.read()
    for secret in secrets:
        assert secret not in raw
    assert any(item["what"].startswith("accounts") for item in manifest["omitted"])
