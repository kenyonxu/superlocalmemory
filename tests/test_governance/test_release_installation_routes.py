"""New release host setup and daemon repair routes share the global boundary."""

# ruff: noqa: F811
import pytest

from tests.test_governance.test_role_surface_matrix_rest import _company, matrix  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v3/local-apps/connect", {"host": "cursor", "tools_profile": "code"}),
        ("/api/v3/local-apps/sandbox-session/reconnect", {}),
        ("/api/daemon/doctor", {}),
        ("/api/daemon/repair", {}),
    ],
)
def test_release_installation_writes_refuse_scoped_admin(matrix, monkeypatch, path, body):
    from superlocalmemory.server.routes import local_apps

    reached = []
    monkeypatch.setattr(local_apps, "_connect", lambda *_: reached.append("connect") or True)
    monkeypatch.setattr(
        local_apps.mcp_processes,
        "request_reconnect",
        lambda *_: reached.append("reconnect") or True,
    )

    async def diagnose(**kwargs):
        reached.append("diagnose")
        return {"success": True, "checks": [], "summary": {}}

    monkeypatch.setattr(local_apps, "run_doctor", diagnose)
    app, client, who, _ = matrix
    _company(app)
    response = client.post(path, json=body, headers=who["admin_b"])
    assert response.status_code == 403, response.text
    assert reached == []
