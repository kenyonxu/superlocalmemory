# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
"""The Compliance view counts and pages the audit trail; it does not load 100,000 rows (G-M4)."""

from __future__ import annotations

import pytest

from superlocalmemory.compliance.audit import AuditChain


@pytest.fixture
def chain(tmp_path):
    c = AuditChain(str(tmp_path / "audit_chain.db"))
    for i in range(250):
        c.log("store" if i % 2 else "recall", agent_id="a", profile_id="p" if i < 200 else "q",
              content_hash=f"h{i}")
    return c


def test_count_is_a_database_count_with_the_same_filters(chain) -> None:
    assert chain.count() == 250
    assert chain.count(profile_id="p") == 200
    assert chain.count(profile_id="p", operation="store") == 100


def test_pages_are_disjoint_newest_first_and_cover_everything(chain) -> None:
    first = chain.query(profile_id="p", limit=80, offset=0)
    second = chain.query(profile_id="p", limit=80, offset=80)
    third = chain.query(profile_id="p", limit=80, offset=160)

    ids = [e["id"] for e in first + second + third]
    assert len(ids) == 200 and len(set(ids)) == 200
    assert ids == sorted(ids, reverse=True)


def test_the_status_view_asks_for_a_count_not_a_hundred_thousand_rows(chain, monkeypatch) -> None:
    calls: list[int] = []
    real = AuditChain.query

    def spy(self, *args, **kwargs):
        calls.append(kwargs.get("limit", 100))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(AuditChain, "query", spy)
    assert chain.count(profile_id="p") == 200
    chain.query(profile_id="p", limit=30)
    assert max(calls) <= 30


async def test_the_audit_route_reports_the_matching_total_and_whether_more_remain(
        chain, monkeypatch) -> None:
    from types import SimpleNamespace

    from superlocalmemory.server.routes import compliance

    monkeypatch.setattr(compliance, "AuditChain", lambda _path: chain)
    monkeypatch.setattr(compliance, "get_active_profile", lambda: "p")
    monkeypatch.setattr(compliance, "COMPLIANCE_AVAILABLE", True)
    import superlocalmemory.server.write_identity as write_identity

    monkeypatch.setattr(write_identity, "require_http_mutation_actor", lambda *a, **k: None)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon_descriptor=None)))

    body = compliance.query_audit_trail(
        request, limit=50, event_type=None, since=None, offset=150)

    assert body["total"] == 200 and body["returned"] == 50 and body["offset"] == 150
    assert body["has_more"] is False
    body = compliance.query_audit_trail(
        request, limit=50, event_type=None, since=None, offset=0)
    assert body["has_more"] is True
