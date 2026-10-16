# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The audit chain detects a change to any field it shows (4.1.25 audit OPS-01).

Version 1 hashed six fields run together with no separators and left the
metadata out, so moving characters from the actor into the workspace, or
rewriting a record's target or outcome, kept every stored hash valid. The
audit's probe printed ``True True True``. Each case below is one of those
edits; every one must now fail verification.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from superlocalmemory.compliance.audit import AuditChain, _compute_hash


@pytest.fixture
def chain(tmp_path):
    path = tmp_path / "audit_chain.db"
    audit = AuditChain(str(path))
    audit.log("store", agent_id="alice", profile_id="ce", content_hash="sha256:aa",
              metadata={"fact_ids": ["f1"], "outcome": "ok"})
    audit.log("ops_resolve:cancel", agent_id="user:u1", profile_id="sales",
              metadata={"operation_id": "op-1", "success": True})
    audit.log("access.role_change", agent_id="user:u1", profile_id="default",
              metadata={"target_user": "u2", "old_role": "admin", "new_role": "viewer"})
    assert audit.verify_integrity() is True
    return path


def _edit(path, sql, *params):
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


@pytest.mark.parametrize("sql", [
    # field-boundary shift: 'alice'+'ce' == 'alicec'+'e'
    "UPDATE audit_chain SET agent_id='alicec', profile_id='e' WHERE id=1",
    "UPDATE audit_chain SET agent_id='mallory' WHERE id=1",
    "UPDATE audit_chain SET profile_id='other' WHERE id=2",
    "UPDATE audit_chain SET operation='ops_resolve:retry' WHERE id=2",
    "UPDATE audit_chain SET content_hash='sha256:bb' WHERE id=1",
    "UPDATE audit_chain SET timestamp='2020-01-01T00:00:00+00:00' WHERE id=3",
    "UPDATE audit_chain SET metadata=json_set(metadata,'$.success',json('false')) WHERE id=2",
    "UPDATE audit_chain SET metadata=json_set(metadata,'$.new_role','admin') WHERE id=3",
    "UPDATE audit_chain SET metadata=json_set(metadata,'$.fact_ids',json('[\"f9\"]')) WHERE id=1",
    "UPDATE audit_chain SET hash_version=1 WHERE id=3",
])
def test_any_field_edit_is_detected(chain, sql):
    _edit(chain, sql)
    report = AuditChain(str(chain)).verify_report()
    assert report["ok"] is False, sql
    assert report["failed_at"] is not None


def test_truncation_and_a_replaced_tail_are_detected(chain):
    _edit(chain, "DELETE FROM audit_chain WHERE id=3")
    assert AuditChain(str(chain)).verify_integrity() is False


def test_a_rewritten_last_entry_with_a_recomputed_hash_is_caught_by_the_anchor(chain):
    """The last entry has no successor to break, so only the anchor sees it."""
    from superlocalmemory.compliance.audit import _compute_hash_v2

    conn = sqlite3.connect(chain)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM audit_chain WHERE id=3").fetchone())
    forged_meta = json.dumps({"new_role": "admin"}, sort_keys=True)
    forged = _compute_hash_v2(row["prev_hash"], row["operation"], row["agent_id"],
                              row["profile_id"], row["content_hash"], row["timestamp"], forged_meta)
    conn.execute("UPDATE audit_chain SET metadata=?, event_hash=? WHERE id=3",
                 (forged_meta, forged))
    conn.commit()
    conn.close()
    report = AuditChain(str(chain)).verify_report()
    assert report["ok"] is False and "anchor" in report["reason"]


def test_concurrent_appends_keep_one_unbroken_chain(tmp_path):
    path = str(tmp_path / "audit_chain.db")
    AuditChain(path)

    def append(n):
        audit = AuditChain(path)
        for i in range(10):
            audit.log("store", agent_id=f"t{n}", metadata={"i": i})

    threads = [threading.Thread(target=append, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    report = AuditChain(path).verify_report()
    assert report == {**report, "ok": True, "entries": 40, "legacy": 0}


def test_an_older_chain_upgrades_in_place_and_is_reported_as_legacy(tmp_path):
    """Entries written before 4.1.25 stay verifiable, but never as fully covered."""
    path = tmp_path / "audit_chain.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE audit_chain (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
        "operation TEXT NOT NULL, agent_id TEXT DEFAULT '', profile_id TEXT DEFAULT '', "
        "content_hash TEXT DEFAULT '', prev_hash TEXT DEFAULT '', event_hash TEXT NOT NULL, "
        "metadata TEXT DEFAULT '{}')")
    prev = "genesis"
    for i in range(3):
        ts = f"2026-10-0{i + 1}T00:00:00+00:00"
        h = _compute_hash(prev, "store", "a", "p", "", ts)
        conn.execute("INSERT INTO audit_chain (timestamp, operation, agent_id, profile_id, "
                     "content_hash, prev_hash, event_hash, metadata) VALUES (?,?,?,?,?,?,?,?)",
                     (ts, "store", "a", "p", "", prev, h, json.dumps({"fact_ids": [i]})))
        prev = h
    conn.commit()
    conn.close()

    audit = AuditChain(str(path))
    audit.log("store", agent_id="b", metadata={"fact_ids": ["new"]})
    report = audit.verify_report()
    assert report["ok"] is True
    assert (report["legacy"], report["fully_covered"]) == (3, 2)
    assert audit.count(operation="anchor_initialized_on_upgrade") == 1

    # A legacy entry's metadata is not covered, and the report says so rather
    # than claiming it: the edit passes, the summary still counts it as legacy.
    _edit(path, "UPDATE audit_chain SET metadata='{}' WHERE id=1")
    assert audit.verify_report()["legacy"] == 3


def test_the_badge_never_says_verified_when_the_check_could_not_run():
    from superlocalmemory.server.audit_view import integrity_summary

    class _Broken:
        def verify_report(self):
            raise sqlite3.DatabaseError("file is not a database")

    summary = integrity_summary(_Broken())
    assert summary["verified"] is False
    assert "not checked" in summary["label"]


def test_audit_rows_name_who_and_what(tmp_path):
    """L-16 / G-L2 / G-H2: the dashboard's columns are filled from the entry."""
    from superlocalmemory.core.audit_record import audit_metadata, content_hash
    from superlocalmemory.server.audit_view import present_event

    audit = AuditChain(str(tmp_path / "audit_chain.db"))
    ctx = {"agent_id": "daemon-capability:abcd", "profile_id": "default",
           "connection_agent": "codex", "fact_ids": ["f1", "f2"],
           "content_hash": content_hash("the memory text")}
    audit.log("store", agent_id=ctx["agent_id"], profile_id="default",
              content_hash=ctx["content_hash"], metadata=audit_metadata(ctx))
    event = present_event(audit.query(limit=1)[0])
    assert event["event_type"] == "store"
    assert "codex" in event["actor"] and "daemon-capability:abcd" in event["actor"]
    assert "2 fact(s)" in event["target"]
    assert event["content_bound"] is True and event["content_hash"].startswith("sha256:")
    assert event["fully_covered"] is True
    assert "the memory text" not in json.dumps(event)
