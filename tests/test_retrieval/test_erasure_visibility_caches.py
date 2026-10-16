"""Erasure also invalidates cached membership and already-hydrated answers."""

from types import SimpleNamespace

import pytest

from superlocalmemory.core.config import RetrievalConfig
from superlocalmemory.core.delete_refusal import _drop_stale_erasure
from superlocalmemory.retrieval.bm25_channel import BM25Channel
from superlocalmemory.retrieval.engine import RetrievalEngine
from tests.test_retrieval.test_erasure_visibility import (  # noqa: F401
    CONTROL,
    HIDDEN,
    tombstone,
)
from tests.test_retrieval.test_erasure_visibility import (
    store as store,
)


def test_warmed_kind_count_and_membership_see_erasure_and_heal(store):
    from superlocalmemory.retrieval.kind_scope import KindMembership

    kinds = KindMembership(store)
    store.visible_count = kinds.count_if_ready
    before = kinds.ids_of('default', 'semantic', .7)
    assert HIDDEN in before
    count = store.get_fact_count('default')
    tombstone(store)
    assert store.get_fact_count('default') == count - 1
    assert HIDDEN not in kinds.ids_of('default', 'semantic', .7)
    assert _drop_stale_erasure(store, 'default', HIDDEN)
    assert store.get_fact_count('default') == count
    assert HIDDEN in kinds.ids_of('default', 'semantic', .7)


def test_profile_channel_hides_erasure_and_restores_after_heal(store):
    from superlocalmemory.retrieval.profile_channel import ProfileChannel
    store.execute("INSERT INTO entity_profiles(profile_entry_id,entity_id,profile_id,"
                  "fact_ids_json) VALUES ('p','e0','default',?)", (f'["{HIDDEN}","{CONTROL}"]',))
    channel = ProfileChannel(store)
    assert HIDDEN in dict(channel.search('Alice', 'default'))
    tombstone(store)
    assert HIDDEN not in dict(channel.search('Alice', 'default'))
    assert CONTROL in dict(channel.search('Alice', 'default'))
    assert _drop_stale_erasure(store, 'default', HIDDEN)
    assert HIDDEN in dict(channel.search('Alice', 'default'))


@pytest.mark.parametrize('deep', [False, True])
def test_erasure_during_rerank_cannot_return_a_hydrated_fact(store, monkeypatch, deep):
    engine = RetrievalEngine(store, RetrievalConfig(use_cross_encoder=False,
                             sufficiency_judge='off'), {'bm25': BM25Channel(store)})
    from superlocalmemory.core.config import ForgettingConfig
    from superlocalmemory.retrieval.forgetting_filter import ForgettingFilter
    engine._registry.register_filter(ForgettingFilter(
        store, ForgettingConfig(enabled=True), deep_recall=deep,
    ).filter)
    engine._reranker = SimpleNamespace(available=True)
    def rerank(query, top, facts, **kwargs):
        assert HIDDEN in facts
        tombstone(store)
        return top, True, 'applied'
    monkeypatch.setattr(engine, '_apply_reranker', rerank)
    try:
        result = engine.recall('Alice memory release', 'default')
        ids = {r.fact.fact_id for r in result.results}
        assert HIDDEN not in ids and ids
    finally:
        engine.close(wait=True)


def test_erased_member_taints_community_context_and_drilldown(store):
    from superlocalmemory.core.community_summary import CommunitySummaryBuilder
    from superlocalmemory.retrieval.community_context import build_community_context
    members = f'["{HIDDEN}","{CONTROL}","f000001"]'
    store.execute("INSERT INTO community_summaries(profile_id,community_id,summary,"
                  "keywords,fact_ids_json,fact_count) VALUES ('default',1,'erasuremarker',"
                  "'erasuremarker',?,3)", (members,))
    results = [SimpleNamespace(fact=SimpleNamespace(fact_id=f)) for f in [CONTROL, 'f000001']]
    builder = CommunitySummaryBuilder(store)
    assert build_community_context(store, results, 'default') is not None
    tombstone(store)
    assert build_community_context(store, results, 'default') is None
    assert builder.get_summary('default', 1) is None
    assert builder.get_summaries('default') == []
    assert _drop_stale_erasure(store, 'default', HIDDEN)
    assert build_community_context(store, results, 'default') is not None
    assert builder.get_summary('default', 1) is not None


def test_authorization_probe_errors_fail_closed(store):
    from superlocalmemory.retrieval.scope_policy import authorized_fact_ids

    tombstone(store)
    class BrokenProbe:
        def execute(self, sql, params=()):
            return store.execute(sql, params)

        def visible_fact_clause(self):
            raise RuntimeError('schema unavailable')
    assert authorized_fact_ids(BrokenProbe(), [HIDDEN, CONTROL], 'default') == set()


def test_execute_only_wrapper_still_enforces_erasure(store):
    from superlocalmemory.retrieval.scope_policy import authorized_fact_ids
    class ExecuteOnly:
        def execute(self, sql, params=()):
            return store.execute(sql, params)
    tombstone(store)
    assert authorized_fact_ids(ExecuteOnly(), [HIDDEN, CONTROL], 'default') == {CONTROL}


def test_cache_readiness_tracks_online_erasure_migrations(store):
    from superlocalmemory.retrieval.kind_scope import KindMembership
    from superlocalmemory.storage.migrations.M035_erasure_receipts import apply as erasure
    from superlocalmemory.storage.migrations.M059_erasure_visibility import apply as currency

    store.execute('DROP TABLE projection_tombstones')
    kinds = KindMembership(store)
    store.visible_count = kinds.count_if_ready
    assert HIDDEN in kinds.ids_of('default', 'semantic', .7)
    count = store.get_fact_count('default')
    with store.raw_connection() as conn:
        erasure(conn)
    tombstone(store)
    assert store.get_fact_count('default') == count - 1
    assert not kinds.available
    with store.raw_connection() as conn:
        currency(conn)
    assert kinds.available
    assert HIDDEN not in kinds.ids_of('default', 'semantic', .7)
    assert _drop_stale_erasure(store, 'default', HIDDEN)
    assert HIDDEN in kinds.ids_of('default', 'semantic', .7)
