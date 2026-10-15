"""Browser trust follows the daemon listener, including credentialed reads."""
# ruff: noqa: F811 -- imported pytest fixtures are requested as parameters

import pytest
from fastapi.testclient import TestClient

from tests.test_server.test_rbac_company_mode_credential import (
    _company_mode,
    app_and_engine,  # noqa: F401
)


@pytest.fixture
def browser_app(app_and_engine):
    app = app_and_engine
    _company_mode(app)
    rbac = app.state.rbac
    uid = rbac.list_users()[0]["user_id"]
    session = rbac.create_session(uid)
    client = TestClient(
        app, base_url=f"http://127.0.0.1:{app.state.daemon_descriptor.port}",
        client=("127.0.0.1", 51000),
    )
    client.cookies.set("slm_session", session)
    return app, client


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8765", "http://localhost:8765",
    "http://127.0.0.1:8767", "http://localhost:8767",
    "http://127.0.0.1:8417", "https://attacker.example",
])
def test_foreign_origin_cannot_read_company_response(browser_app, origin):
    _, client = browser_app
    response = client.get("/api/memories", headers={"Origin": origin})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options("/api/memories", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_bound_origin_can_read_company_response(browser_app, host):
    app, client = browser_app
    origin = f"http://{host}:{app.state.daemon_descriptor.port}"
    response = client.get("/api/memories", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.parametrize("origin_kind", ["own", "foreign"])
def test_install_token_has_no_cors_permission(browser_app, origin_kind):
    from superlocalmemory.core.security_primitives import ensure_install_token

    ensure_install_token()
    app, client = browser_app
    port = app.state.daemon_descriptor.port if origin_kind == "own" else 8767
    origin = f"http://127.0.0.1:{port}"
    response = client.get("/internal/token", headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options("/internal/token", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in preflight.headers
    # Relative dashboard fetches need no CORS permission.
    assert client.get("/internal/token").status_code == 200


def test_startup_cors_uses_kernel_bound_port(tmp_path, monkeypatch):
    import os

    from superlocalmemory.server import unified_daemon as daemon

    monkeypatch.setenv("SLM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SLM_DISABLE_LEGACY_PORT", "1")
    monkeypatch.setenv("SLM_DAEMON_PORT", os.environ["SLM_DAEMON_PORT"])
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)
    monkeypatch.setattr(daemon, "_start_record_guardian", lambda: None)
    monkeypatch.setattr(daemon, "_stop_record_guardian", lambda: None)
    observed = []

    def serve(port, host, listener, lock):
        assert port == listener.getsockname()[1]
        assert port not in (0, 8765, 8767)
        app = daemon.create_app()
        client = TestClient(app, base_url=f"http://127.0.0.1:{port}",
                            client=("127.0.0.1", 51000))
        response = client.get("/health", headers={"Origin": f"http://localhost:{port}"})
        observed.append(response.headers.get("access-control-allow-origin"))
        assert observed[-1] == f"http://localhost:{port}"

    monkeypatch.setattr(daemon, "_serve_owned", serve)
    daemon.start_server(0)
    assert len(observed) == 1


def test_default_install_cors_without_binding_live_port(monkeypatch):
    from superlocalmemory.server import unified_daemon as daemon

    monkeypatch.setenv("SLM_DAEMON_PORT", "8765")
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)
    app = daemon.create_app()
    client = TestClient(app, client=("127.0.0.1", 51000))
    for host in ("localhost", "127.0.0.1"):
        origin = f"http://{host}:8765"
        response = client.get("/health", headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == origin
    assert "access-control-allow-origin" not in client.get(
        "/health", headers={"Origin": "http://localhost:8767"},
    ).headers


def test_legacy_port_is_never_granted_cors(monkeypatch):
    from superlocalmemory.server import unified_daemon as daemon

    monkeypatch.setenv("SLM_DAEMON_PORT", "8767")
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", None)
    client = TestClient(daemon.create_app(), client=("127.0.0.1", 51000))
    assert "access-control-allow-origin" not in client.get(
        "/health", headers={"Origin": "http://localhost:8767"},
    ).headers
