"""A relative expression needs an event anchor, not the daemon clock."""

from superlocalmemory.encoding.date_resolution import keep_supported_dates, try_parse_date
from superlocalmemory.encoding.source_fidelity import check_fact_against_source
from superlocalmemory.storage.models import AtomicFact


def test_unknown_event_date_does_not_use_wall_clock():
    assert try_parse_date("yesterday") is None
    assert try_parse_date("last Tuesday") is None


def test_wrong_relative_weekday_is_rejected():
    source = "Priya deploys next Tuesday."
    assert not check_fact_against_source(
        "Priya deploys on 2026-03-16.", source, reference_date="2026-03-14"
    ).ok
    assert check_fact_against_source(
        "Priya deploys on 2026-03-17.", source, reference_date="2026-03-14"
    ).ok


def test_unrelated_relative_expression_does_not_license_any_date():
    source = "I renewed yesterday. The contract ends on 2026-12-31."
    assert not check_fact_against_source(
        "The contract ends on 2026-03-13.", source, reference_date="2026-03-14"
    ).ok


def test_model_date_field_is_checked_against_event_date():
    source = "Priya deploys next Tuesday."
    facts = keep_supported_dates(
        [AtomicFact(content=source, referenced_date="2026-03-16")],
        source,
        reference_date="2026-03-14",
    )
    assert facts[0].referenced_date in (None, "2026-03-17")


def test_bad_event_anchor_does_not_allow_wall_clock_dates():
    assert not check_fact_against_source(
        "The user met Priya on 2026-10-10.", "I met Priya yesterday.", reference_date="not a date"
    ).ok


def test_written_source_year_cannot_change():
    assert not check_fact_against_source(
        "The user moved to Pune on 2026-05-14.", "I moved to Pune in May 2020."
    ).ok
    assert not check_fact_against_source(
        "Priya renewed on 2027-10-02.", "Priya renewed on 2 October 2026."
    ).ok


def test_mode_a_month_year_is_not_a_partial_day_token():
    from superlocalmemory.core.config import EncodingConfig
    from superlocalmemory.encoding.fact_extractor import FactExtractor, _extract_date_string

    assert _extract_date_string("I moved to Pune in May 2020.") == "May 2020"
    facts = FactExtractor(EncodingConfig()).extract_facts(
        ["I moved to Pune in May 2020."], "test", "2026-03-14"
    )
    assert facts[0].referenced_date.startswith("2020-05")
