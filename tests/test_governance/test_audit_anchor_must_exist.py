# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""PR 248 review, finding 4: a chain with entries and no anchor is not "intact".

Deleting the last entry together with the ``.anchor`` sidecar used to verify
as ``ok: true``: without the anchor, truncation cannot be checked, and the
report said nothing. It is now reported, so the dashboard badge is not green.
"""

from __future__ import annotations

import os
import sqlite3

from superlocalmemory.compliance.audit import AuditChain


def _chain(tmp_path, entries: int = 5):
    path = tmp_path / "audit_chain.db"
    chain = AuditChain(path)
    for i in range(entries):
        chain.log("access.role_change", agent_id="user:a", profile_id="default",
                  metadata={"i": i, "new_role": "viewer"})
    return path


def test_the_reviewers_reproduction_tail_and_anchor_deleted(tmp_path):
    path = _chain(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM audit_chain WHERE id=5")
    conn.commit()
    conn.close()
    os.remove(str(path) + ".anchor")
    report = AuditChain(path).verify_report()
    assert report["ok"] is False
    assert "no anchor" in report["reason"]
    assert report["entries"] == 4


def test_an_unreadable_anchor_is_reported_too(tmp_path):
    path = _chain(tmp_path)
    with open(str(path) + ".anchor", "w", encoding="utf-8") as fh:
        fh.write("not json")
    report = AuditChain(path).verify_report()
    assert report["ok"] is False
    assert "anchor" in report["reason"]


def test_an_intact_anchored_chain_still_verifies(tmp_path):
    report = AuditChain(_chain(tmp_path)).verify_report()
    assert report["ok"] is True and report["reason"] == ""


def test_an_empty_chain_needs_no_anchor(tmp_path):
    path = tmp_path / "audit_chain.db"
    chain = AuditChain(path)
    assert chain.verify_report()["ok"] is True


def test_the_next_entry_does_not_hide_a_removed_anchor(tmp_path):
    path = _chain(tmp_path)
    os.remove(str(path) + ".anchor")
    chain = AuditChain(path)
    assert chain.verify_report()["ok"] is False
    chain.log("access.login", agent_id="user:a", profile_id="")
    assert chain.verify_report()["ok"] is False
    assert not os.path.exists(str(path) + ".anchor")
