"""Hopfield's large-store searches run side by side (4.2.4).

The channel held its matrix-cache lock around every search. On a large store
the search never touches that cache (ANN prefilter, per-call reads), yet
concurrent recalls queued behind each other: ~120 ms of work took 1.7-4.2 s
on the author's 27k-fact store with a few agents recalling at once.
"""

import threading
import time

import numpy as np

from superlocalmemory.math.hopfield import HopfieldConfig
from superlocalmemory.retrieval.hopfield_channel import HopfieldChannel

DIM = 8


class _VectorStore:
    available = True

    def search(self, query, top_k, profile_id):
        time.sleep(0.2)  # the KNN read
        return [("f1", 0.9), ("f2", 0.8)]


class _Db:
    def get_fact_count(self, profile_id, include_global=False, include_shared=False):
        return 50_000  # above the prefilter threshold

    def get_external_visible_embeddings(self, *a, **k):
        return []

    def get_fact_embeddings_by_ids(self, ids, profile_id, **k):
        return [(i, np.ones(DIM, dtype=np.float32)) for i in ids]

    def execute(self, sql, params=()):
        return []


def test_four_concurrent_large_store_searches_overlap():
    channel = HopfieldChannel(_Db(), _VectorStore(), HopfieldConfig(dimension=DIM))
    query = np.ones(DIM, dtype=np.float32)
    threads = [threading.Thread(target=channel.search, args=(query, "default", 10))
               for _ in range(4)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start < 0.5  # serialised it was >= 0.8 s
