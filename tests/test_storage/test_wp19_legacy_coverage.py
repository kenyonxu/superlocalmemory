"""A store shaped like a real upgraded one converges to full description.

Synthetic, but proportioned on the owner's measured store: an old slice with
no tags/project and no dates, mostly rule-guessed kinds, a few caller and model
kinds, a share of operational stubs, and an empty bitemporal start everywhere.
"""

import json
import sqlite3

import pytest

from superlocalmemory.storage import schema
from superlocalmemory.storage.database import DatabaseManager
from superlocalmemory.storage.models import AtomicFact, MemoryRecord

N = 100
PEOPLE = ('Priya', 'Arjun', 'Meera', 'Kabir')
TOOLS = ('PostgreSQL', 'Redis', 'Kubernetes', 'Grafana')


def _text(i):
    if i % 10 == 9:
        return '[] session ended 2026-04-0%d' % (i % 9 + 1)
    person, tool = PEOPLE[i % 4], TOOLS[(i // 4) % 4]
    return (f'Zephyr is our project. {person} moved the billing jobs to {tool} '
            f'on 2026-03-{i % 28 + 1:02d}. #infra note {i}.')


@pytest.fixture
def legacy(tmp_path):
    from superlocalmemory.storage.migrations.M013_bi_temporal_columns import repair
    from superlocalmemory.storage.migrations.M060_memory_description import apply
    db = DatabaseManager(tmp_path / 'memory.db')
    db.initialize(schema)
    with sqlite3.connect(db.db_path) as conn:
        repair(conn)
        apply(conn)
    for i in range(N):
        text = _text(i)
        caller = i % 33 == 0
        metadata = {'tags': ['caller-tag'], 'project': 'CallerProject'} if caller else {}
        db.store_memory(MemoryRecord(memory_id=f'm{i}', content=text, metadata=metadata,
                                    session_id=f's{i // 5}',
                                    created_at='2026-04-01T10:00:00+00:00'))
        source = 'caller' if caller else ('model:llm' if i % 13 == 0 else 'rules')
        db.store_fact(AtomicFact(fact_id=f'f{i}', memory_id=f'm{i}', content=text,
                                 memory_kind='fact', memory_kind_source=source,
                                 created_at='2026-04-01T10:00:00+00:00'))
    # What a pre-fix store looks like: nothing below was ever written.
    db.execute("UPDATE atomic_facts SET observation_date=NULL, referenced_date=NULL, "
               "valid_from=NULL, non_answer=0, entities_json='[]', "
               "canonical_entities_json='[]'")
    db.execute('UPDATE fact_temporal_validity SET valid_from=NULL')
    db.execute('DELETE FROM fact_entity_associations')
    yield db
    db.close()


def _pct(cov, key, total):
    return round(100 * cov[key] / total)


def _snapshot(db):
    return {t: [dict(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')]
            for t in ('memories', 'atomic_facts', 'fact_temporal_validity',
                      'fact_entity_associations')}


def test_legacy_store_coverage_rises_and_undo_restores_exactly(legacy):
    from superlocalmemory.core.enrichment import EnrichmentRunner
    from superlocalmemory.core.metadata_health import coverage
    db = legacy
    before_rows = _snapshot(db)
    contents = {r['fact_id']: r['content'] for r in before_rows['atomic_facts']}
    before = coverage(db)
    assert _pct(before, 'valid_from', N) == 0
    assert _pct(before, 'observation_dates', N) == 0
    assert before['tags'] == 4 and before['non_answer'] == 0

    runner = EnrichmentRunner(db, idle=lambda: True)
    for _ in range(40):
        result = runner.run_batch(limit=10)
        # Recall keeps working at every step of the run.
        assert db.search_facts_fts('PostgreSQL', 'default')
        if result['status'] == 'completed':
            break
    assert result['status'] == 'completed' and result['processed'] == N
    after = coverage(db)
    print('coverage before', before)
    print('coverage after', after)
    stubs = N // 10
    assert after['valid_from'] == N
    assert after['observation_dates'] == N
    assert after['event_dates'] >= N - stubs  # every dated sentence has its date
    assert after['entities'] >= N - stubs
    assert after['non_answer'] == stubs
    assert after['tags'] >= N - stubs and after['project'] >= N - stubs
    # Content is never rewritten; caller and model values are never replaced.
    rows = {r['fact_id']: dict(r) for r in db.execute('SELECT * FROM atomic_facts')}
    assert {k: r['content'] for k, r in rows.items()} == contents
    assert all(rows[f'f{i}']['memory_kind_source'] == 'caller' for i in range(0, N, 33))
    assert rows['f13']['memory_kind_source'] == 'model:llm'
    assert json.loads(db.execute("SELECT metadata_json FROM memories WHERE memory_id='m33'")[0][
        'metadata_json']) == {'tags': ['caller-tag'], 'project': 'CallerProject'}
    # Stubs stay stored and listed (flagged), never hydrated as recall answers.
    listed = [f for f in db.get_all_facts('default') if 'session ended' in f.content]
    assert len(listed) == stubs and all(f.non_answer for f in listed)
    ids = [f.fact_id for f in listed]
    assert db.get_facts_by_ids(ids, 'default', exclude_non_answer=True) == []

    runner.undo(result['run_id'])
    assert _snapshot(db) == before_rows


def test_doctor_report_reads_coverage_without_writing(legacy, tmp_path):
    from superlocalmemory.core.metadata_health import from_path
    db = legacy
    before = _snapshot(db)
    report = from_path(db.db_path)
    assert report['coverage']['facts'] == N
    assert report['coverage']['kind_guessed'] > report['coverage']['kind_confirmed']
    assert report['enrichment']['status'] == 'waiting_for_idle'
    assert report['summary'].startswith('Improving your memories: 0 of')
    assert _snapshot(db) == before
    missing = from_path(tmp_path / 'absent' / 'memory.db')
    assert missing['enrichment']['status'] == 'unavailable'
