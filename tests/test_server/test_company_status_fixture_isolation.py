"""A delayed prior daemon startup must not leak its identity into another test."""

from dataclasses import replace

from tests.test_server.test_rbac_company_mode_credential import (
    _company_mode,
    app_and_engine,
)
from tests.test_server.test_status_in_company_mode import DISCOVERY_KEYS, _client


def test_prior_starting_descriptor_does_not_leak(engine_with_mock_deps, monkeypatch):
    from superlocalmemory.infra.daemon_identity import build_descriptor
    from superlocalmemory.server import unified_daemon as daemon

    prior = build_descriptor(port=0, version="prior-test", state="starting")
    monkeypatch.setattr(daemon, "_ACTIVE_DAEMON_DESCRIPTOR", prior)
    app = app_and_engine.__wrapped__(engine_with_mock_deps, monkeypatch)
    _company_mode(app)

    body = _client(app).get("/status").json()

    assert set(body) == DISCOVERY_KEYS
    assert body["port"] > 0 and body["version"] != prior.version
    assert app.state.daemon_descriptor != replace(prior, port=body["port"])
