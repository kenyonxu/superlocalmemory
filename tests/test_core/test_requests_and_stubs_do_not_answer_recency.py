"""'What am I working on?' is answered with memories, not bookkeeping or the user's own prompts.

The first example a new user clicks in the Recall Lab. Measured on the author's
real store: of 5,443 facts written in 7 days about 1,056 memories were hook
bookkeeping and 45 captured prompts split into ~316 facts, the question routes
as "recency", the newest rows win, and the newest are noise. A captured prompt
is an instruction the user gave an agent, not a fact about the world: it stays
searchable, but it never answers a present-tense question ahead of a memory an
agent or the user saved, and it ranks below those otherwise.
"""

from __future__ import annotations

import pytest

from tests.conftest import force_sync_enrichment

STUBS = (
    '[Agentic_official] session ended 2026-10-13 11:01',
    '[Agentic_official] session ended 2026-10-13 09:54 | files: NOTICE, formats.md',
    'File changed: NOTICE',
)
PROMPTS = (
    'please fix the flaky login test in the billing service',
    'rename the dashboard tab to Recall Lab and rerun the build',
)
AGENT_MEMORIES = (
    'Migrating the search index to the new embedding model.',
    'Reviewing the dashboard redesign with the design team.',
)
CAPTURE = {'source': 'auto-capture', 'category': 'decision'}


def _age(engine, content_prefix, hours):
    engine._db.execute(
        "UPDATE atomic_facts SET created_at = datetime('now', ?) WHERE content LIKE ?",
        (f'-{hours} hours', content_prefix + '%'))


@pytest.fixture
def store(engine_with_mock_deps):
    """Agent memories are oldest; the newest rows are the noise, as on the real store."""
    engine = force_sync_enrichment(engine_with_mock_deps)
    for text in AGENT_MEMORIES:
        engine.store(text, metadata={'agent_id': 'claude-code'})
        _age(engine, text, 48)
    for text in PROMPTS:
        engine.store(text, metadata=CAPTURE)
        _age(engine, text, 1)
    for text in STUBS:
        engine.store(text)
    return engine


def _texts(response):
    return [r.fact.content for r in response.results]


def test_the_newest_rows_really_are_the_noise(store):
    rows = store._db.execute('SELECT content, non_answer FROM atomic_facts '
                             'ORDER BY created_at DESC')
    bookkeeping = [r for r in rows if 'session ended' in r['content']
                   or r['content'].startswith('File changed')]
    newest = [r['content'] for r in rows[:len(bookkeeping)]]
    assert newest == [r['content'] for r in bookkeeping]
    # ingestion derives a second copy of each session line; every one is flagged
    assert len(bookkeeping) >= len(STUBS)
    assert all(r['non_answer'] == 1 for r in bookkeeping), [dict(r) for r in bookkeeping]


def test_what_am_i_working_on_returns_agent_memories_first_and_no_stub(store):
    response = store.recall('What am I working on?', limit=10)
    assert response.query_type == 'recency'
    texts = _texts(response)
    assert not [t for t in texts if t in STUBS or t.startswith('File changed')
                or 'session ended' in t], texts
    agent_places = [i for i, t in enumerate(texts) if t in AGENT_MEMORIES]
    prompt_places = [i for i, t in enumerate(texts) if t in PROMPTS]
    assert len(agent_places) == len(AGENT_MEMORIES), texts
    assert not prompt_places or max(agent_places) < min(prompt_places), texts


def test_the_recency_channel_does_not_spend_its_slots_on_stubs_or_prompts(store):
    from superlocalmemory.retrieval.temporal_channel import TemporalChannel

    ids = [fid for fid, _ in TemporalChannel(store._db).search(
        'What am I working on?', store._profile_id, query_type='recency')]
    contents = {r['fact_id']: r['content'] for r in store._db.execute(
        'SELECT fact_id, content FROM atomic_facts')}
    shown = {contents[i] for i in ids}
    assert set(AGENT_MEMORIES) <= shown
    assert shown.isdisjoint(PROMPTS) and not [t for t in shown if 'session ended' in t]
    assert not [t for t in shown if t.startswith('File changed')]


def test_a_question_about_what_the_user_asked_brings_the_prompts_back(store):
    response = store.recall('what did I ask you to do right now', limit=10)
    assert response.query_type == 'recency'
    assert set(PROMPTS) <= set(_texts(response))
    assert not [t for t in _texts(response) if t in STUBS]


def test_prompts_stay_searchable_by_what_they_say(store):
    response = store.recall('flaky login test billing service', limit=10)
    assert PROMPTS[0] in _texts(response)


def test_a_prompt_ranks_below_an_agent_memory_of_equal_relevance(engine_with_mock_deps):
    engine = force_sync_enrichment(engine_with_mock_deps)
    words = 'the staging database listens on port 5433'
    engine.store(words + ' (typed by the user)', metadata=CAPTURE)
    engine.store(words + ' (saved by the agent)', metadata={'agent_id': 'claude-code'})
    texts = _texts(engine.recall('which port does the staging database listen on', limit=5))
    assert texts[0].endswith('(saved by the agent)'), texts
    assert any(t.endswith('(typed by the user)') for t in texts), texts


@pytest.mark.parametrize('query,expected', [
    ('what did I ask you to do', True),
    ('what have I been asking you this week', True),
    ('show my prompts from yesterday', True),
    ('what did I tell you about the build', True),
    ('which requests did I make', True),
    ('What am I working on?', False),
    ('what is the staging database port', False),
    ('what did the team decide about the port', False),
])
def test_the_question_that_is_about_requests_is_recognised(query, expected):
    from superlocalmemory.retrieval.request_memories import asks_about_requests

    assert asks_about_requests(query) is expected


def test_a_burst_of_prompts_does_not_leave_the_recency_channel_short(tmp_path):
    """Sixty prompts typed in a row fill the newest fifty slots; the read widens."""
    from superlocalmemory.retrieval.temporal_channel import TemporalChannel
    from superlocalmemory.storage import schema
    from superlocalmemory.storage.database import DatabaseManager

    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    try:
        def add(i, metadata, age_minutes):
            db.execute("INSERT INTO memories (memory_id, profile_id, content, metadata_json) "
                       "VALUES (?, 'default', 'x', ?)", (f'm{i}', metadata))
            db.execute(
                "INSERT INTO atomic_facts (fact_id, memory_id, profile_id, content, created_at) "
                "VALUES (?, ?, 'default', ?, datetime('now', ?))",
                (f'f{i:03d}', f'm{i}', f'fact {i}', f'-{age_minutes} minutes'))
        for i in range(60):
            add(i, '{"source": "auto-capture"}', i)
        for i in range(60, 65):
            add(i, '{"agent_id": "claude-code"}', 600 + i)
        add(65, 'not json at all', 700)  # a malformed row must not fail the read
        ids = [f for f, _ in TemporalChannel(db)._recency_fallback('default', False, False)]
        assert ids == [f'f{i:03d}' for i in (60, 61, 62, 63, 64, 65)]
        asked = [f for f, _ in TemporalChannel(db)._recency_fallback(
            'default', False, False, skip_requests=False)]
        assert asked[:3] == ['f000', 'f001', 'f002'] and len(asked) == 50
    finally:
        db.close()


class _Db:
    def __init__(self, request_ids):
        self.request_ids = request_ids

    def execute(self, sql, params=()):
        return [{'fact_id': i} for i in params if i in self.request_ids]


def _results(spec):
    from dataclasses import dataclass, field
    from types import SimpleNamespace

    @dataclass(frozen=True)
    class R:
        fact: object
        ranking_score: float
        score: float = 0.0
        rank_position: int = 0
        evidence_chain: list = field(default_factory=list)

    return [R(SimpleNamespace(fact_id=fid), score) for fid, score in spec]


def _order(results):
    return [r.fact.fact_id for r in results]


def test_settling_puts_prompts_after_everything_on_a_present_tense_question():
    from superlocalmemory.retrieval.request_memories import settle_requests

    results = _results([('p1', 9.0), ('a1', 2.0), ('p2', 8.0), ('a2', 1.0)])
    out = settle_requests(results, _Db({'p1', 'p2'}), 'What am I working on?', 'recency')
    assert _order(out) == ['a1', 'a2', 'p1', 'p2']
    assert [r.rank_position for r in out] == [1, 2, 3, 4]
    assert 'captured_prompt' in out[2].evidence_chain
    assert _order(results) == ['p1', 'a1', 'p2', 'a2']  # the input is not modified


def test_settling_lowers_a_prompt_only_a_little_on_any_other_question():
    from superlocalmemory.retrieval.request_memories import settle_requests

    out = settle_requests(_results([('p1', 3.0), ('a1', 2.0), ('a2', 1.0), ('p2', -1.0)]),
                          _Db({'p1', 'p2'}), 'which port does staging use', 'factual')
    # 3.0 -> 1.5 falls below 2.0 but not below 1.0; -1.0 sinks further, never rises
    assert _order(out) == ['a1', 'p1', 'a2', 'p2']
    assert out[1].ranking_score == pytest.approx(1.5)
    assert out[3].ranking_score == pytest.approx(-1.5)


def test_settling_changes_nothing_when_the_question_is_about_requests_or_none_are():
    from superlocalmemory.retrieval.request_memories import settle_requests

    results = _results([('p1', 9.0), ('a1', 2.0)])
    assert settle_requests(results, _Db({'p1'}), 'what did I ask you to do', 'recency') is results
    assert settle_requests(results, _Db(set()), 'What am I working on?', 'recency') is results
    assert settle_requests([], _Db({'p1'}), 'What am I working on?', 'recency') == []


def test_a_store_that_cannot_answer_leaves_the_order_alone():
    from superlocalmemory.retrieval.request_memories import settle_requests

    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError('database is locked')

    results = _results([('p1', 9.0), ('a1', 2.0)])
    assert settle_requests(results, Broken(), 'What am I working on?', 'recency') is results
