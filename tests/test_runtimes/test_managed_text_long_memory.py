"""A memory longer than the worker's limit is embedded from its start, never refused.

A refusal stopped a whole re-index to EmbeddingGemma 2 at the first long memory.
"""

from superlocalmemory.runtimes import managed_text
from superlocalmemory.runtimes.worker_limits import MAX_TEXT_CHARS, check_texts


def test_long_texts_are_cut_to_the_worker_limit():
    long, short = "x" * (MAX_TEXT_CHARS + 500), "a short memory"
    fitted = managed_text._fit([long, short])
    assert len(fitted[0]) == MAX_TEXT_CHARS and fitted[0] == long[:MAX_TEXT_CHARS]
    assert fitted[1] == short
    check_texts(fitted)  # the worker's own check now passes


def test_the_strict_reindex_path_sends_fitted_texts(monkeypatch):
    sent = []

    class Client:
        def embed_texts(self, texts, *, prompt):
            check_texts(texts)
            sent.extend(texts)
            return [[0.0] * 4 for _ in texts]

    emb = managed_text.ManagedTextEmbedder.__new__(managed_text.ManagedTextEmbedder)
    emb._closed = False
    emb._config = type("C", (), {"dimension": 4})()
    monkeypatch.setattr(emb, "_client", lambda: Client(), raising=False)
    out = emb._embed(["y" * (MAX_TEXT_CHARS * 2)], "Document", strict=True)
    assert len(out) == 1 and len(sent[0]) == MAX_TEXT_CHARS
