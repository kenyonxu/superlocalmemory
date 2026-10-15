# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Account administration: inside its authority, never headless, always on record.

* OPS-27: an admin of ``default`` reset the password of an account that also
  administers ``sales``, then signed in as it.
* UX-20: one dropdown change removed the last administrator.
* UX-21: the sign-in screen pointed to a reset control that did not exist; the
  owner's recovery path is ``slm team admin`` / ``slm team reset-password``.
* OPS-21: no account or role event reached the hash-chained audit trail.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import json
import threading

import pytest

from superlocalmemory.access.rbac import LastAdminError
from tests.test_governance.test_role_surface_matrix_rest import matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def _audit(app):
    from superlocalmemory.compliance.audit import AuditChain

    db = app.state.rbac._db_path
    from pathlib import Path

    return AuditChain(str(Path(db).parent / "audit_chain.db"))


# -- OPS-27 ------------------------------------------------------------------------


def test_a_scoped_admin_cannot_reset_an_account_that_administers_elsewhere(matrix):
    app, client, who, ids = matrix
    app.state.rbac.set_require_login(True)
    r = client.patch(f"/api/rbac/users/{ids['dual']}", json={"password": "hijacked-123"},
                     headers=who["admin_a"])
    assert r.status_code == 403, r.text
    assert app.state.rbac.verify_credentials("dual", "hijacked-123") is None
    for body in ({"status": "disabled"}, {"display_name": "Mallory"}):
        assert client.patch(f"/api/rbac/users/{ids['dual']}", json=body,
                            headers=who["admin_a"]).status_code == 403, body
    assert client.delete(f"/api/rbac/users/{ids['dual']}",
                         headers=who["admin_a"]).status_code == 403


def test_the_scoped_admin_can_still_change_that_accounts_role_where_they_manage(matrix):
    app, client, who, ids = matrix
    app.state.rbac.set_require_login(True)
    r = client.post("/api/rbac/members", json={"user_id": ids["dual"], "role": "member"},
                    headers=who["admin_a"])
    assert r.status_code == 200, r.text


def test_an_admin_of_every_workspace_of_the_account_may_reset_it(matrix):
    app, client, who, ids = matrix
    app.state.rbac.set_require_login(True)
    r = client.patch(f"/api/rbac/users/{ids['viewer_a']}", json={"password": "new-password-1"},
                     headers=who["admin_a"])
    assert r.status_code == 200, r.text
    assert app.state.rbac.verify_credentials("viewer_a", "new-password-1")


def test_a_person_changes_their_own_password_by_proving_the_current_one(matrix):
    app, client, who, ids = matrix
    app.state.rbac.set_require_login(True)
    uid = ids["dual"]
    wrong = client.patch(f"/api/rbac/users/{uid}", json={"password": "new-password-2"},
                         headers=who["dual"])
    assert wrong.status_code == 403
    ok = client.patch(f"/api/rbac/users/{uid}",
                      json={"password": "new-password-2", "current_password": "password-1234"},
                      headers=who["dual"])
    assert ok.status_code == 200, ok.text


def test_the_owner_resets_any_account_in_personal_mode(matrix):
    app, client, who, ids = matrix
    r = client.patch(f"/api/rbac/users/{ids['dual']}", json={"password": "owner-set-123"},
                     headers=who["owner"])
    assert r.status_code == 200, r.text


# -- UX-20 ------------------------------------------------------------------------


def test_the_last_admin_cannot_demote_or_remove_themselves(matrix):
    app, client, who, ids = matrix
    app.state.rbac.set_require_login(True)
    me = ids["admin_a"]
    demote = client.post("/api/rbac/members", json={"user_id": me, "role": "viewer"},
                         headers=who["admin_a"])
    remove = client.request("DELETE", "/api/rbac/members", json={"user_id": me},
                            headers=who["admin_a"])
    assert (demote.status_code, remove.status_code) == (409, 409), (demote.text, remove.text)
    assert "administrator" in demote.json()["detail"]
    assert app.state.rbac.get_role(me, "default").value == "admin"


def test_the_owner_is_held_to_it_too(matrix):
    app, client, who, ids = matrix
    me = ids["admin_a"]
    assert client.patch(f"/api/rbac/users/{me}", json={"status": "disabled"},
                        headers=who["owner"]).status_code == 409
    assert client.delete(f"/api/rbac/users/{me}", headers=who["owner"]).status_code == 409


def test_with_a_second_admin_the_first_may_step_down(matrix):
    app, client, who, ids = matrix
    rbac = app.state.rbac
    rbac.set_membership("default", ids["member_a"], "admin")
    r = client.post("/api/rbac/members", json={"user_id": ids["admin_a"], "role": "viewer"},
                    headers=who["admin_a"])
    assert r.status_code == 200, r.text


def test_two_admins_demoting_each_other_at_once_leave_one_admin(matrix):
    app, _, _, ids = matrix
    rbac = app.state.rbac
    rbac.set_membership("default", ids["member_a"], "admin")
    outcomes = []

    def demote(uid):
        try:
            rbac.set_membership("default", uid, "viewer")
            outcomes.append("ok")
        except LastAdminError:
            outcomes.append("refused")

    threads = [threading.Thread(target=demote, args=(uid,))
               for uid in (ids["admin_a"], ids["member_a"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "refused"]
    admins = [m for m in rbac.list_members("default") if m["role"] == "admin"]
    assert len(admins) == 1


# -- OPS-21 ------------------------------------------------------------------------


def test_account_events_reach_the_audit_chain_in_order(matrix):
    app, client, who, ids = matrix
    owner = who["owner"]
    client.post("/api/rbac/users", json={"username": "newbie", "password": "password-1234",
                                         "role": "viewer"}, headers=owner)
    uid = {u["username"]: u["user_id"] for u in app.state.rbac.list_users()}["newbie"]
    client.post("/api/rbac/members", json={"user_id": uid, "role": "member"}, headers=owner)
    client.post("/api/rbac/login", json={"username": "newbie", "password": "wrong-password"})
    login = client.post("/api/rbac/login", json={"username": "newbie", "password": "password-1234"})
    client.post("/api/rbac/logout", headers={"X-SLM-User-Session": login.json()["token"]})
    client.post("/api/rbac/members", json={"user_id": ids["admin_a"], "role": "viewer"},
                headers=owner)
    client.delete(f"/api/rbac/users/{uid}", headers=owner)

    rows = list(reversed(_audit(app).query(limit=100)))
    seen = [(r["operation"], json.loads(r["metadata"]).get("outcome")) for r in rows
            if r["operation"].startswith("access.")]
    assert seen == [
        ("access.user_create", "requested"), ("access.user_create", "ok"),
        ("access.role_change", "requested"), ("access.role_change", "ok"),
        ("access.login", "failed"), ("access.login", "ok"), ("access.logout", "ok"),
        ("access.role_change", "requested"), ("access.role_change", "refused"),
        ("access.user_delete", "requested"), ("access.user_delete", "ok"),
    ]
    change = next(json.loads(r["metadata"]) for r in rows
                  if r["operation"] == "access.role_change")
    assert change["old_role"] == "viewer" and change["new_role"] == "member"
    text = json.dumps(rows)
    assert "password-1234" not in text and login.json()["token"] not in text
    assert _audit(app).verify_integrity() is True


def test_a_change_the_audit_cannot_record_is_not_made(matrix, monkeypatch):
    import superlocalmemory.compliance.audit as audit_mod

    app, client, who, ids = matrix

    def _down(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(audit_mod.AuditChain, "log", _down)
    r = client.post("/api/rbac/members", json={"user_id": ids["viewer_a"], "role": "admin"},
                    headers=who["owner"])
    assert r.status_code == 503, r.text
    assert app.state.rbac.get_role(ids["viewer_a"], "default").value == "viewer"


# -- UX-21: the owner's recovery path ---------------------------------------------


@pytest.mark.parametrize("sub", ["admin", "reset-password"])
def test_slm_team_offers_the_recovery_commands(sub, capsys):
    import argparse

    from superlocalmemory.cli.team_cmd import register_team_parser

    parser = argparse.ArgumentParser()
    register_team_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["team", sub, "alice"])
    assert args.team_command == sub and args.username == "alice"


def test_reset_password_goes_through_the_audited_route(monkeypatch, capsys):
    import argparse

    from superlocalmemory.cli import team_cmd

    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/users"):
            return {"users": [{"user_id": "u1", "username": "alice"}]}
        return {"ok": True}

    monkeypatch.setattr(team_cmd, "daemon_request", fake)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("brand-new-pass\n"))
    team_cmd.cmd_team(argparse.Namespace(team_command="reset-password", username="alice",
                                         password_stdin=True, json=False))
    assert calls[-1] == ("PATCH", "/api/rbac/users/u1", {"password": "brand-new-pass"})
    assert "brand-new-pass" not in capsys.readouterr().out


def test_the_sign_in_screen_points_to_controls_that_exist():
    from pathlib import Path

    import superlocalmemory

    ui = Path(superlocalmemory.__file__).parent / "ui" / "js"
    gate = (ui / "od-auth-gate.js").read_text(encoding="utf-8")
    team = (ui / "od-team.js").read_text(encoding="utf-8")
    assert "Settings › Access" not in gate
    assert "Access & Users" in gate and "slm team reset-password" in gate
    assert "Reset password" in team and "od-team-reset-save" in team
