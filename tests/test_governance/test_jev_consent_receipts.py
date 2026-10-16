# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""Consent to send memories to Jev leaves a receipt (4.1.25 audit OPS-19).

The audit turned consent on and off and the audit count stayed 255 -> 255 ->
255: the setting held only the latest boolean, so who agreed, when and to
which wording was unrecoverable. Each grant and withdrawal now appends a
receipt before the setting changes, and no receipt means no change.
"""

# ruff: noqa: F811  (imported pytest fixtures are re-declared as test parameters)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import superlocalmemory
from superlocalmemory.server.consent_receipts import ANSWER_CHECK_NOTICE, RERANK_NOTICE, notice_text
from tests.test_server.test_answer_check_api import (  # noqa: F401 — fixtures
    _reset_fake_key_store,
    _retrieval,
    call_order,
    client,
)


def _receipts():
    from superlocalmemory.compliance.audit import AuditChain
    from superlocalmemory.infra.data_root import state_path

    rows = AuditChain(str(state_path("audit_chain.db"))).query(limit=50)
    return [(r["operation"], r["agent_id"], json.loads(r["metadata"]))
            for r in reversed(rows) if r["operation"].startswith("consent.")]


def _consent(client, accepted):
    return client.post("/api/v3/answer-check/jev/consent",
                       json={"accepted": accepted, "provider": "typesafe"})


def test_grant_and_withdrawal_each_leave_a_receipt(client):
    assert _consent(client, True).status_code == 200
    assert _consent(client, True).status_code == 200  # no change, no second receipt
    assert _consent(client, False).status_code == 200
    receipts = _receipts()
    assert [r[2]["decision"] for r in receipts] == ["granted", "withdrawn"]
    granted = receipts[0][2]
    assert granted["notice_version"] == ANSWER_CHECK_NOTICE[0]
    assert granted["notice_sha256"] == hashlib.sha256(
        notice_text("answer_check", "typesafe").encode("utf-8")).hexdigest()
    assert granted["provider"] == "typesafe" and granted["sends"]
    assert all(r[1] for r in receipts)  # an actor on every receipt


def test_no_receipt_no_change(client, monkeypatch, tmp_path):
    import superlocalmemory.compliance.audit as audit_mod

    def _down(*_a, **_k):
        raise OSError("audit store unavailable")

    monkeypatch.setattr(audit_mod.AuditChain, "log", _down)
    response = _consent(client, True)
    assert response.status_code == 503, response.text
    assert _retrieval(tmp_path)["sufficiency_jev_consent"] is False


def test_the_notice_hashed_is_the_notice_shown():
    js = (Path(superlocalmemory.__file__).parent / "ui" / "js" / "answer-check.js").read_text(
        encoding="utf-8")
    shown = ANSWER_CHECK_NOTICE[1]
    assert "'When on, each recall sends your question and its top ' +" in js
    for piece in ("3 memories to {provider}. Don’t use this for client, confidential ",
                  "or personal material."):
        assert piece in js and piece.replace("{provider}", "") in shown.replace("{provider}", "")
    assert RERANK_NOTICE[1] in js
