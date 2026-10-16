"""Causal assertions require a supported endpoint and temporal order."""
from unittest.mock import MagicMock

import pytest

from superlocalmemory.encoding.graph_builder import GraphBuilder
from superlocalmemory.storage.models import AtomicFact


def fact(fid, text, date):
    return AtomicFact(fact_id=fid, content=text, canonical_entities=["atlas"],
                      observation_date=date, created_at=date)


@pytest.mark.parametrize("earlier", [
    "Atlas uses PostgreSQL 17", "Atlas shipped the mobile app", "Atlas hired Priya",
])
def test_unrelated_prior_entity_facts_are_not_causes(earlier):
    db = MagicMock()
    db.get_facts_by_entity.return_value = [fact("old", earlier, "2026-01-01")]
    db.execute.return_value = []
    new = fact("new", "Atlas rollout was delayed because the vendor audit failed", "2026-01-02")
    assert GraphBuilder(db)._build_causal_edges(new, "default") == []


@pytest.mark.parametrize("date,wording,expected", [
    ("2026-01-01", "Atlas rollout was delayed because the vendor audit failed", 1),
    ("2026-01-03", "Atlas rollout was delayed because the vendor audit failed", 0),
    ("2025-01-01", "Atlas rollout was delayed because the vendor audit failed", 0),
    ("2026-01-01", "Atlas rollout was delayed not because the vendor audit failed", 0),
    ("2026-01-01", "Atlas postponed rollout in order to pass the vendor audit", 0),
    ("2026-01-01", "Atlas might delay rollout because the vendor audit failed", 0),
])
def test_explicit_cause_requires_order_window_and_assertion(date, wording, expected):
    db = MagicMock()
    db.get_facts_by_entity.return_value = [fact("old", "Atlas vendor audit failed", date)]
    db.execute.return_value = []
    edges = GraphBuilder(db)._build_causal_edges(fact("new", wording, "2026-01-02"), "default")
    assert len(edges) == expected
    if edges:
        assert (edges[0].source_id, edges[0].target_id) == ("old", "new")
