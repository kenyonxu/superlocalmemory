# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later — see LICENSE file
"""UX-12: primary and degraded search agree with the stored shared decision."""

# ruff: noqa: F811 (pytest fixture imported and redeclared as a parameter)

from __future__ import annotations

import pytest

from tests.test_server.test_recall_serializer_kind_fields import _response
from tests.test_server.test_search_fallback_shows_memory_kind import client  # noqa: F401


@pytest.mark.parametrize("primary", [True, False], ids=["engine", "degraded"])
def test_search_keeps_the_canonical_scope_kind_project_and_audience(client, monkeypatch, primary):
    tc, headers = client
    engine = tc.app.state.engine
    db = engine._db
    db.execute("UPDATE atomic_facts SET scope='shared', shared_with='[\"sales\"]' "
               "WHERE fact_id='fact-confirmed'")
    db.execute("UPDATE memories SET scope='shared', shared_with='[\"sales\"]', "
               "metadata_json=json_set(metadata_json,'$.project','Atlas') "
               "WHERE memory_id=(SELECT memory_id FROM atomic_facts "
               "WHERE fact_id='fact-confirmed')")
    if primary:
        fact = db.get_fact("fact-confirmed")
        monkeypatch.setattr(engine, "recall", lambda *a, **k: _response(fact))
    response = tc.post("/api/search", json={"query": "zorblatt", "kind": "decision"},
                       headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["results"]
    assert len(rows) == 1
    row = rows[0]
    assert row["fact_id"] == "fact-confirmed"
    assert row["scope"] == "shared"
    assert row["shared_with"] == ["sales"]
    assert row["project_name"] == "Atlas"
    assert row["memory_kind"] == "decision"
    assert row["memory_kind_label"] == "Decision"
    assert row["memory_kind_state"] == "confirmed"
