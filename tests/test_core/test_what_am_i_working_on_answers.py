""""What am I working on" is answered with the newest memories, not abstained.

Its answer shares no word with the question, so the word-support gate used to
call every right answer weak and say "That isn't in SuperLocalMemory."
"""

from tests.conftest import force_sync_enrichment


def test_a_present_tense_question_gets_recent_memories(engine_with_mock_deps, monkeypatch):
    from superlocalmemory.core import support_stage

    # On a real store the word-support gate abstains here (no word of the
    # question is in its answer); the mock embedder hides that, so assert the
    # routing: a present-tense question never reaches the gate.
    def gate(*a, **kw):
        raise AssertionError("a recency question must not face the word-support gate")

    monkeypatch.setattr(support_stage, "assess", gate)
    engine = force_sync_enrichment(engine_with_mock_deps)
    engine._config.retrieval.answer_support_gate = True  # the shipped default
    for text in ("Shipping the release candidate of the billing service tonight.",
                 "Reviewing the dashboard redesign with the design team.",
                 "Migrating the search index to the new embedding model."):
        engine.store(text)
    response = engine.recall("what am I working on", limit=5)
    assert response.query_type == "recency"
    assert response.results, response.abstention_note
    assert not response.abstained
    assert "isn't in" not in (response.abstention_note or "")


def test_an_ordinary_question_still_faces_the_support_gate(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    engine.store("The staging database listens on port 5433.")
    response = engine.recall("qwpoe zzxv asdkj", limit=5)
    assert response.query_type != "recency"
    assert not [r for r in response.results if getattr(r, "support_tier", "") == "supported"]
