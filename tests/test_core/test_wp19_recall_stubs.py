"""Recall opts in to excluding stubs; listing keeps them (review H3)."""

from __future__ import annotations

from tests.test_core.test_wp19_resave_valid_from import engine  # noqa: F401


def test_recall_never_answers_with_a_stub_but_listing_shows_it(engine) -> None:  # noqa: F811
    stub = 'Session ID: 9f8e7d6c5b4a'
    engine.store(content=stub, session_date='2026-03-14')
    engine.store(content='The session about Zephyr chose PostgreSQL.',
                 session_date='2026-03-14')
    flagged = engine._db.execute('SELECT fact_id FROM atomic_facts WHERE non_answer=1')
    assert flagged, 'the stub was not saved and flagged, so this proves nothing'
    stub_ids = {r['fact_id'] for r in flagged}
    result = engine.recall('Session ID 9f8e7d6c5b4a')
    assert not stub_ids & {r.fact.fact_id for r in result.results}
    listed = {f.fact_id for f in engine._db.get_all_facts(engine.profile_id)}
    assert stub_ids <= listed
