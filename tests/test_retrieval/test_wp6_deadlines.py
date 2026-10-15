"""WP-6: reloads and late replies respect interactive recall contracts."""

import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

from superlocalmemory.retrieval import channel_status
from superlocalmemory.retrieval.query_embedding import QueryEmbedder
from superlocalmemory.retrieval.sufficiency import LayaSufficiencyJudge


def test_warm_embedding_has_a_deadline():
    class Slow:
        is_warm = True
        has_loaded_once = True

        def embed(self, text):
            time.sleep(0.12)
            return [1.0]

    qe = QueryEmbedder(lambda: Slow())
    try:
        began = time.monotonic()
        vector, status = qe.embed("question", 0.01)
        assert vector is None
        assert status == channel_status.TIMEOUT
        assert time.monotonic() - began < 0.10
    finally:
        qe.close()


def test_completed_late_reply_survives_idle_gap():
    read_fd, write_fd = os.pipe()
    judge = LayaSufficiencyJudge(start=False)
    proc = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: read_fd))
    judge._stale = (1, time.monotonic() - 31)
    judge._ready = True
    judge._worker_lost = Mock()
    os.write(write_fd, b'{"id":1,"ok":true}\n')
    try:
        assert judge._drain_stale(proc, time.monotonic() + 0.1)
        judge._worker_lost.assert_not_called()
        assert judge.ready
    finally:
        os.close(read_fd)
        os.close(write_fd)
        judge.shutdown()


def test_legacy_nomic_vocabulary_is_advisory_even_for_an_oov_only_phrase(monkeypatch):
    from superlocalmemory.core.config import EmbeddingConfig
    from superlocalmemory.retrieval import query_lexicon

    monkeypatch.setattr(query_lexicon, "vocabulary", lambda: frozenset({"who", "owns", "how"}))
    native = SimpleNamespace(_config=EmbeddingConfig(), embed=Mock(return_value=[1.0]))
    qe = QueryEmbedder(lambda: native)
    try:
        assert qe.embed("zxqv plorth wimble", 0.1) == ([1.0], None)
        native.embed.assert_called_once_with("zxqv plorth wimble")
        assert qe.embed("who owns wimble", 0.1) == ([1.0], None)
    finally:
        qe.close()
