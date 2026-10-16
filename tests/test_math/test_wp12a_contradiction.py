"""Angular distance is not logical contradiction."""
from unittest.mock import MagicMock

import pytest

from superlocalmemory.math.sheaf import SheafConsistencyChecker
from superlocalmemory.storage.models import AtomicFact, GraphEdge


@pytest.mark.parametrize("left,right,conflict", [
    ("Priya enjoys hiking", "Priya uses Python", False),
    ("Priya uses Python", "Priya uses Rust", False),
    ("Priya's status is active", "Priya's status is inactive", True),
    ("Priya likes hiking", "Priya does not like hiking", True),
    ("Priya's status is active", "Joan's status is inactive", False),
])
def test_contradiction_requires_same_subject_attribute_incompatible_value(left, right, conflict):
    db = MagicMock()
    a = AtomicFact(fact_id="a", content=left, canonical_entities=["person"], embedding=[1., 0.])
    b = AtomicFact(fact_id="b", content=right, canonical_entities=["person"], embedding=[0., 1.])
    checker = SheafConsistencyChecker(db, contradiction_threshold=.45)
    assert bool(checker.detect_contradictions_batch([a, b], "default")) == conflict
    db.get_edges_for_node.return_value = [GraphEdge(source_id="a", target_id="b")]
    db.get_fact.return_value = b
    db.execute.return_value = [{"embedding": "[0,1]", "content": right,
                                "observation_date": None, "referenced_date": None}]
    assert bool(checker.check_consistency(a, "default")) == conflict


def test_incompatible_birthday_can_be_reported_in_different_sessions():
    from superlocalmemory.math.contradiction_evidence import incompatible

    a = AtomicFact(content="Priya's birthday is January 1", observation_date="2026-01-01")
    b = AtomicFact(content="Priya's birthday is May 3", observation_date="2026-01-02")
    assert incompatible(a, b)


def test_explicit_different_event_dates_can_describe_compatible_changes():
    from superlocalmemory.math.contradiction_evidence import incompatible

    a = AtomicFact(content="Priya's status is active", referenced_date="2026-01-01")
    b = AtomicFact(content="Priya's status is inactive", referenced_date="2026-01-02")
    assert not incompatible(a, b)
