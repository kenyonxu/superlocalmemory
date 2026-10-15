from types import SimpleNamespace

from superlocalmemory.retrieval.query_embedding import QueryEmbedder


def test_queries_cached_in_one_space_never_answer_after_provider_swap():
    holder = {'embedder': SimpleNamespace(embed=lambda text: [1.0, 0.0])}
    query = QueryEmbedder(lambda: holder['embedder'])
    try:
        assert query.embed('same question', 1) == ([1.0, 0.0], None)
        holder['embedder'] = SimpleNamespace(embed=lambda text: [0.0, 1.0])
        assert query.embed('same question', 1) == ([0.0, 1.0], None)
    finally:
        query.close()


def test_a_late_old_space_reply_cannot_poison_the_new_cache():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from superlocalmemory.retrieval import channel_status

    entered, release = threading.Event(), threading.Event()
    def old_embed(text):
        entered.set()
        release.wait(2)
        return [1.0, 0.0]
    holder = {'embedder': SimpleNamespace(embed=old_embed)}
    query = QueryEmbedder(lambda: holder['embedder'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(query.embed, 'same question', 1.0)
        entered.wait(1)
        holder['embedder'] = SimpleNamespace(embed=lambda text: [0.0, 1.0])
        try:
            assert query.embed('same question', 1) == ([0.0, 1.0], None)
            release.set()
            assert pending.result() == (None, channel_status.WARMING)
            assert query.cache['same question'] == [0.0, 1.0]
        finally:
            release.set()
            query.close()


def test_a_stale_submission_cannot_be_joined_by_the_new_space():
    import threading

    release = threading.Event()
    def slow(text):
        release.wait(2)
        return [1.0, 0.0]
    holder = {'embedder': SimpleNamespace(embed=slow)}
    query = QueryEmbedder(lambda: holder['embedder'])
    old, generation = query._current_provider()
    holder['embedder'] = SimpleNamespace(embed=lambda text: [0.0, 1.0])
    query._current_provider()
    try:
        query._future_for('same question', old, generation)
        assert query.embed('same question', 0.1) == ([0.0, 1.0], None)
    finally:
        release.set()
        query.close()
