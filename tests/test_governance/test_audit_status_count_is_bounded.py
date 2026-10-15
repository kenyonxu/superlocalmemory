# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""G-M4: count all matching events while fetching only the bounded recent page."""

# ruff: noqa: F811 (pytest fixture imported and redeclared as a parameter)

from fastapi.testclient import TestClient

from superlocalmemory.compliance.audit import AuditChain

# Load the route before per-test/launcher data-root selection. Its audit path
# must remain dynamic; patching the path here would hide the import-time defect.
from superlocalmemory.server.routes import compliance  # noqa: F401
from tests.test_server.test_rbac_company_mode_credential import app_and_engine  # noqa: F401


def test_compliance_count_is_scoped_and_never_loads_a_large_page(app_and_engine, monkeypatch):
    app = app_and_engine
    audit = AuditChain(app.state.config.db_path.parent / "audit_chain.db")
    for profile in ["default"] * 37 + ["sales"] * 4:
        audit.log("store", agent_id="fixture-agent", profile_id=profile)
    assert audit.count() == 41
    assert audit.count(profile_id="default") == 37
    limits = []
    original = AuditChain.query

    def query(self, *args, **kwargs):
        limits.append(kwargs["limit"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuditChain, "query", query)
    descriptor = app.state.daemon_descriptor
    headers = {"X-SLM-Daemon-Capability": descriptor.capability,
               "X-SLM-Target-Instance": descriptor.instance_id}
    response = TestClient(app).get("/api/compliance/status", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["audit_events_count"] == 37
    assert len(response.json()["recent_audit_events"]) == 30
    assert limits == [30]
