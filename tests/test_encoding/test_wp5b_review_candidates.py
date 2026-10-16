"""Rejected model statements remain reviewable without entering the trusted list."""

import json
from unittest.mock import Mock

import pytest

from superlocalmemory.core.config import EncodingConfig
from superlocalmemory.encoding.fact_extractor import FactExtractor
from superlocalmemory.storage.models import Mode

SOURCE = "The Kestrel checkpoint was recalled at rank 1 in 2004.6 ms."
GOOD = "The Kestrel checkpoint recall took 2004.6 ms"
BAD = "The recall happened on June 1st, 2004"


def _extract(items, source=SOURCE, *, unbounded=False):
    llm = Mock()
    llm.is_available.return_value = True
    llm.generate.return_value = json.dumps(items)
    extractor = FactExtractor(EncodingConfig(), llm=llm, mode=Mode.B)
    if unbounded:
        return extractor._extract_llm([source], "wp5b", "2026-03-14", "", "")
    return extractor.extract_facts([source], "wp5b", "2026-03-14")


def test_rejected_statement_is_retained_separately_from_supported_facts():
    facts = _extract([{"text": BAD}, {"text": GOOD}])
    assert [f.content for f in facts] == [GOOD]
    assert [f.content for f in getattr(facts, "review_candidates", ())] == [BAD]


def test_rejected_only_response_preserves_candidates_through_rules_fallback():
    facts = _extract([{"text": BAD}])
    assert any("2004.6 ms" in f.content for f in facts)
    assert all(f.content != BAD for f in facts)
    assert [f.content for f in getattr(facts, "review_candidates", ())] == [BAD]


@pytest.mark.parametrize("field", ["referenced_date", "interval_start", "interval_end"])
def test_faithful_measurement_stays_live_and_loses_invented_date_fields(field):
    facts = _extract([{"text": GOOD, field: "2004-06-01"}])
    assert [f.content for f in facts] == [GOOD]
    assert getattr(facts[0], field) is None
    assert not getattr(facts, "review_candidates", ())


def test_review_candidates_are_bounded_and_deduplicated():
    facts = _extract([{"text": BAD}] * 10)
    assert len(getattr(facts, "review_candidates", ())) == 1


def test_every_parsed_rejected_statement_is_preserved_for_review():
    rejected = [f"The recall took {2100 + i}.6 ms" for i in range(10)]
    facts = _extract([{"text": text} for text in rejected])
    assert {f.content for f in getattr(facts, "review_candidates", ())} == set(rejected)


def test_ambiguous_model_speaker_is_reviewable_instead_of_dropped():
    source = "[Alex]: I use Rust for payroll. [Priya]: I use Rust for payroll."
    facts = _extract([{"text": "I use Rust for payroll."}], source=source, unbounded=True)
    assert all(f.content != "I use Rust for payroll." for f in facts)
    assert [f.content for f in getattr(facts, "review_candidates", ())] == [
        "I use Rust for payroll."
    ]
