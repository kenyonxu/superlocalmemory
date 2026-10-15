# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""L-15/G-M2 and L-16/G-L2: reproduce real HTTP save provenance locally."""

from __future__ import annotations

import hashlib
import json

import pytest

from superlocalmemory.compliance.audit import AuditChain
from superlocalmemory.core.engine_ingestion import build_engine_ingestion_command
from superlocalmemory.core.ingestion_command import IngestionState
from superlocalmemory.core.security_primitives import ensure_install_token
from tests.test_server.test_canonical_remember_route import _client


@pytest.mark.parametrize("private_capability", [True, False], ids=["connection", "claimed"])
def test_save_binds_agent_claims_and_audit_content(engine_with_mock_deps, private_capability):
    engine = engine_with_mock_deps
    text = "Ship Atlas on Friday after the security review."
    with _client(engine) as client:
        if not private_capability:
            client.headers.pop("X-SLM-Daemon-Capability")
            client.headers.pop("X-SLM-Target-Instance")
            client.headers["X-Install-Token"] = ensure_install_token()
        response = client.post("/remember?wait=true", headers={"X-SLM-Connection-Agent": "codex"},
                               json={"content": text, "idempotency_key": "attribution-probe",
                                     "metadata": {"agent_id": "mallory",
                                                  "agent_id_source": "connection"}})
        assert response.status_code == 200, response.text
        op = dict(engine._db.execute("SELECT * FROM ingestion_operations")[0])
        metadata = json.loads(op["raw_metadata_json"])
        assert metadata["agent_id"] == ("codex" if private_capability else "mallory")
        assert metadata["agent_id_source"] == ("connection" if private_capability else "claimed")
        if private_capability:
            assert metadata["claimed_agent_id"] == "mallory"
        else:
            assert "claimed_agent_id" not in metadata
    # The fixture owns a canonical writer, but no daemon background materializer.
    # A durable/queryable receipt is not a completed store or a post-hook event.
    done = build_engine_ingestion_command(engine).materialize(op["operation_id"])
    assert done.state is IngestionState.COMPLETE
    audit = AuditChain(engine._config.db_path.parent / "audit_chain.db")
    events = audit.query(operation="store")
    assert events
    for event in events:
        assert event["agent_id"] == op["trusted_actor_id"]
        assert event["content_hash"] == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        details = json.loads(event["metadata"])
        assert details["claimed_agent_id"] == "mallory"
        if private_capability:
            assert details["connection_agent"] == "codex"
        else:
            assert "connection_agent" not in details
    assert audit.verify_integrity() is True
