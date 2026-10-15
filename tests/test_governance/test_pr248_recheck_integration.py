"""Permanent PR 248 integration probes for workspace permissions."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from superlocalmemory.server.profile_runtime import get_profile_runtime
from tests.test_governance.test_role_surface_matrix_mcp import store  # noqa: F401
from tests.test_governance.test_role_surface_matrix_rest import _company, matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import (  # noqa: F401
    _daemon_headers,
    app_and_engine,
)


def _active(client, headers):
    return client.get("/api/profiles", headers=headers).json()["active_profile"]


# -- Finding 1 ------------------------------------------------------------------------


@pytest.mark.parametrize("scoped,home", [("admin_a", "default"), ("admin_b", "sales")])
def test_f1_scoped_admin_cannot_turn_company_mode_off(matrix, scoped, home):
    app, client, who, _ = matrix
    _company(app)
    client.post(f"/api/profiles/{home}/switch", headers=who[scoped])
    r = client.post("/api/rbac/policy", json={"require_login": False}, headers=who[scoped])
    assert r.status_code == 403, r.text[:200]
    assert app.state.rbac.require_login() is True
    for space in ("default", "sales"):
        assert client.get("/api/tiers/stats", params={"profile_id": space}).status_code == 401


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v3/answer-check/jev/consent", {"consent": True}),
        ("/api/v3/answer-check/jev/key", {"key": "x"}),
        ("/api/v3/answer-check/mode", {"mode": "off"}),
        ("/api/memory-kinds/settings", {"jev_consent": True, "backend": "jev"}),
    ],
)
def test_f1_scoped_admin_cannot_flip_installation_wide_consent(matrix, path, body):
    """A scoped admin of sales must not set installation-wide consent/keys."""
    app, client, who, _ = matrix
    _company(app)
    client.post("/api/profiles/sales/switch", headers=who["admin_b"])
    r = client.post(path, json=body, headers=who["admin_b"])
    assert r.status_code in (401, 403), (path, r.status_code, r.text[:300])


def test_f1_personal_mode_owner_keeps_policy_and_settings(matrix):
    app, client, who, _ = matrix
    r = client.post("/api/rbac/policy", json={"require_login": True}, headers=who["owner"])
    assert r.status_code == 200, r.text[:200]
    r = client.post("/api/rbac/policy", json={"require_login": False}, headers=who["owner"])
    # owner with no session, login now required: needs the capability
    assert r.status_code in (200, 401, 403)
    cap = _daemon_headers(app)
    r = client.post("/api/rbac/policy", json={"require_login": False}, headers=cap)
    assert r.status_code == 200, r.text[:200]
    assert app.state.rbac.require_login() is False


def test_f1_personal_mode_no_accounts_owner_full_access(app_and_engine):
    """No team accounts at all: the machine owner changes global settings and reads."""
    from fastapi.testclient import TestClient

    from superlocalmemory.core.security_primitives import ensure_install_token
    from superlocalmemory.server.rbac_enforce import holds_global_manage
    from tests.test_server.test_rbac_company_mode_credential import LOOPBACK

    app = app_and_engine
    tok = {"X-Install-Token": ensure_install_token()}
    c = TestClient(
        app, base_url="http://127.0.0.1:8765", client=LOOPBACK, raise_server_exceptions=False
    )
    assert c.get("/api/memories", headers=tok).status_code == 200
    assert c.get("/api/profiles", headers=tok).status_code == 200
    r = c.post("/api/rbac/policy", json={"require_login": False}, headers=tok)
    assert r.status_code == 200, r.text[:200]
    assert holds_global_manage(None, "x") is False
    from starlette.requests import Request

    from superlocalmemory.server.mutation_gate import mutation_gate

    for path in (
        "/api/v3/mode",
        "/api/v3/provider",
        "/api/optimize/config",
        "/api/v3/answer-check/jev/consent",
        "/api/backup/create",
    ):
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in tok.items()],
            "app": app,
            "client": LOOPBACK,
            "server": ("127.0.0.1", 8765),
        }
        assert mutation_gate(Request(scope), app.state) is None, path


# -- Finding 2 ------------------------------------------------------------------------


def test_f2_two_users_switching_do_not_affect_each_other(matrix):
    app, client, who, _ = matrix
    _company(app)
    assert client.get("/api/memories", headers=who["viewer_a"]).status_code == 200
    assert client.post("/api/profiles/sales/switch", headers=who["dual"]).status_code == 200
    assert client.get("/api/memories", headers=who["viewer_a"]).status_code == 200
    assert _active(client, who["viewer_a"]) == "default"
    assert _active(client, who["dual"]) == "sales"
    assert client.post("/api/profiles/default/switch", headers=who["viewer_a"]).status_code == 200
    assert _active(client, who["dual"]) == "sales"
    assert get_profile_runtime(app.state).snapshot.profile_id == "default"


def test_f2_switch_from_mcp_shaped_request_is_session_scoped(matrix):
    """daemon_request from the HTTP MCP process sends capability + the caller session."""
    app, client, who, _ = matrix
    _company(app)
    headers = {**_daemon_headers(app), "X-SLM-User-Session": who["dual"]["X-SLM-User-Session"]}
    r = client.post("/api/profiles/sales/switch", headers=headers)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("active_profile") == "sales"
    # record whether the daemon moved (expected: no, session scope)
    assert get_profile_runtime(app.state).snapshot.profile_id == "default"
    assert body.get("scope") == "session"


def test_f2_viewer_cannot_switch_to_unreadable_workspace(matrix):
    app, client, who, _ = matrix
    _company(app)
    r = client.post("/api/profiles/sales/switch", headers=who["viewer_a"])
    assert r.status_code == 403, r.text[:200]
    r = client.post("/api/profiles/nosuch/switch", headers=who["viewer_a"])
    assert r.status_code in (403, 404)
    assert "sales" not in r.text, f"switch error names a foreign workspace: {r.text[:300]}"


# -- OPS-09 ---------------------------------------------------------------------------


@pytest.mark.parametrize("company", [True, False])
@pytest.mark.parametrize(
    "who_name,expect",
    [
        ("viewer_a", {"default"}),
        ("member_a", {"default"}),
        ("admin_a", {"default"}),
        ("admin_b", {"sales"}),
        ("dual", {"default", "sales"}),
    ],
)
def test_ops09_member_lists_only_readable(matrix, company, who_name, expect):
    app, client, who, _ = matrix
    if company:
        _company(app)
    r = client.get("/api/profiles", headers=who[who_name])
    assert r.status_code == 200, r.text[:200]
    names = {p["name"] for p in r.json()["profiles"]}
    assert names == expect, (who_name, names)
    assert r.json()["total_profiles"] == len(expect)


def test_ops09_anonymous_in_company_mode_gets_nothing(matrix):
    app, client, who, _ = matrix
    _company(app)
    r = client.get("/api/profiles")
    assert r.status_code == 401, (r.status_code, r.text[:200])
    r = client.get("/api/profiles", headers=who["owner"])  # install token, no session
    assert r.status_code in (401, 403) or "sales" not in r.text, r.text[:200]


def test_ops09_owner_personal_sees_all(matrix):
    app, client, who, _ = matrix
    names = {
        p["name"] for p in client.get("/api/profiles", headers=who["owner"]).json()["profiles"]
    }
    assert {"default", "sales"} <= names


# -- Finding 5 ------------------------------------------------------------------------


def test_f5_installation_events_visible_to_global_admin_not_scoped(matrix):
    app, client, who, ids = matrix
    from tests.test_governance.test_role_surface_matrix_rest import _global_admin

    _global_admin(app, who, ids)
    _company(app)
    # generate an installation-wide event: a policy change by the root admin, then back
    client.post("/api/rbac/policy", json={"require_login": True}, headers=who["root_admin"])
    rg = client.get("/api/compliance/audit", headers=who["root_admin"])
    ra = client.get("/api/compliance/audit", headers=who["admin_b"])
    assert rg.status_code == 200, rg.text[:200]
    assert rg.json().get("includes_installation_events") is True
    assert any((e.get("profile_id") or "") == "" for e in rg.json()["events"]) or any(
        "installation" in json.dumps(e) for e in rg.json()["events"]
    )
    if ra.status_code == 200:
        assert ra.json().get("includes_installation_events") is False
        for e in ra.json()["events"]:
            assert (e.get("profile_id") or "sales") == "sales", e


# -- Finding 4 ------------------------------------------------------------------------


def test_f4_truncation_with_anchor_removed_not_intact(tmp_path):
    from superlocalmemory.compliance.audit import AuditChain

    db = tmp_path / "audit.db"
    chain = AuditChain(str(db))
    for i in range(4):
        chain.log(operation=f"op{i}", agent_id="a", profile_id="default")
    assert chain.verify_report()["ok"] is True
    conn = sqlite3.connect(db)
    tbl = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    t = "audit_events" if "audit_events" in tbl else tbl[0]
    conn.execute(f"DELETE FROM {t} WHERE rowid = (SELECT MAX(rowid) FROM {t})")
    conn.commit()
    conn.close()
    anchors = [p for p in tmp_path.iterdir() if p.name.endswith(".anchor")]
    assert anchors, list(tmp_path.iterdir())
    for p in anchors:
        p.unlink()
    rep = AuditChain(str(db)).verify_report()
    assert rep["ok"] is False, rep
    assert "anchor" in json.dumps(rep), rep


def test_f4_empty_chain_without_anchor_is_fine(tmp_path):
    from superlocalmemory.compliance.audit import AuditChain

    rep = AuditChain(str(tmp_path / "audit.db")).verify_report()
    assert rep["ok"] is True, rep


# -- Finding 1 residual: store-wide integrity repair -------------------------------------


def test_f1_scoped_admin_store_wide_integrity_repair(matrix, monkeypatch):
    from superlocalmemory.storage import store_check

    started = []
    monkeypatch.setattr(store_check, "start_repair", lambda *a, **k: started.append(1) or True)
    app, client, who, _ = matrix
    _company(app)
    client.post("/api/profiles/sales/switch", headers=who["admin_b"])
    r = client.post("/api/integrity/repair-now", headers=who["admin_b"])
    assert r.status_code in (401, 403), (r.status_code, r.text[:200], started)


# -- Finding 2 residual: HTTP MCP switch_profile moves the shared MCP engine --------------

_MCP_SWITCH = r"""
import asyncio, json, sys
from superlocalmemory.access.rbac import RbacEngine
from superlocalmemory.infra.data_root import canonical_data_root
from superlocalmemory.mcp import server as srvmod
from superlocalmemory.core.caller_session import bind_session
import superlocalmemory.cli.daemon as d

rbac = RbacEngine(str(canonical_data_root() / "memory.db"))
rbac.set_require_login(True)
d.is_daemon_running = lambda: True
calls = []
def fake(method, path, *a, **k):
    calls.append(path)
    name = path.split("/")[3]
    return {"success": True, "active_profile": name, "scope": "session"}
d.daemon_request = fake

async def main():
    out = {}
    eng = srvmod.get_engine()
    out["before"] = eng.profile_id
    bind_session(sys.argv[1])  # admin_b
    r = await srvmod.server.call_tool("switch_profile", {"profile_id": "sales"})
    out["switch"] = (r.structured_content or {})
    out["after"] = srvmod.get_engine().profile_id
    from superlocalmemory.storage.models import AtomicFact, MemoryRecord
    m = MemoryRecord(profile_id="sales", content="SALES-SECRET-PROBE")
    eng._db.store_memory(m)
    eng._db.store_fact(AtomicFact(memory_id=m.memory_id, profile_id="sales",
                                 content="SALES-SECRET-PROBE"))
    bind_session(sys.argv[2])  # viewer_a: viewer of default only
    try:
        res = await srvmod.server.read_resource("slm://recent")
        out["viewer_recent"] = str(res)[:600]
    except Exception as exc:
        out["viewer_recent"] = "refused:" + repr(exc)[:200]
    bind_session("")
    out["calls"] = calls
    print("REPORT" + json.dumps(out, default=str))

asyncio.run(main())
"""


def test_f2_http_mcp_switch_profile_moves_shared_engine(store):
    from tests.test_governance.test_role_surface_matrix_mcp import store as _s  # noqa: F401

    rbac, ids = store
    env = {**os.environ, "SLM_DAEMON_PORT": "1"}
    env.pop("SLM_USER_SESSION", None)
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            _MCP_SWITCH,
            rbac.create_session(ids["admin_b"]),
            rbac.create_session(ids["viewer_a"]),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("REPORT")), None)
    assert line, done.stderr[-3000:]
    rep = json.loads(line[len("REPORT") :])
    assert rep["after"] == rep["before"], rep
